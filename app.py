from flask import Flask, request, jsonify
import os, requests
from dotenv import load_dotenv
from base64 import b64encode
from openai import OpenAI

# 🌱 Load environment variables from .env
load_dotenv()

# 🌐 Flask App
app = Flask(__name__)

# ✅ Track handled Slack event IDs
handled_event_ids = set()

# 🔐 Environment Variables
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 🧠 Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# 🔐 Jira Basic Auth Header
def get_jira_auth_header():
    token = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    return {
        "Authorization": f"Basic {b64encode(token.encode()).decode()}",
        "Content-Type": "application/json"
    }

# ✅ Send a message to Slack
def send_slack_message(channel_id, message):
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel_id, "text": message}
    )

# 🧠 Keep state across messages
conversation_states = {}

# 📥 Slack Events Endpoint
@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    # ✅ Slack URL verification
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]}), 200, {'Content-Type': 'application/json'}

    # 🔐 Event deduplication
    event_id = data.get("event_id")
    if event_id in handled_event_ids:
        return jsonify({"ok": True})
    handled_event_ids.add(event_id)

    event = data.get("event", {})
    user_msg = event.get("text", "").strip()
    user_id = event.get("user")
    channel_id = event.get("channel")

    # 🛑 Skip bot or empty messages
    if not user_msg or "bot_id" in event:
        return jsonify({"ok": True})

    # 💬 Multi-step conversation
    convo = conversation_states.get(user_id, {})

    if not convo:
        conversation_states[user_id] = {"step": "ask_summary"}
        send_slack_message(channel_id, "📝 What is the task summary?")
    elif convo["step"] == "ask_summary":
        convo["summary"] = user_msg
        convo["step"] = "ask_due"
        send_slack_message(channel_id, "🗕️ When is it due?")
    elif convo["step"] == "ask_due":
        convo["due_raw"] = user_msg
        convo["step"] = "ask_priority"
        send_slack_message(channel_id, "❗ How important is it? (e.g., Low, Medium, High)")
    elif convo["step"] == "ask_priority":
        convo["priority"] = user_msg.capitalize()
        convo["step"] = "create_issue"

        # ✅ Use OpenAI to get clean due date
        try:
            gpt_due = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Extract the due date in format YYYY-MM-DD only."},
                    {"role": "user", "content": convo["due_raw"]}
                ],
                max_tokens=10
            )
            due_date = gpt_due.choices[0].message.content.strip()
        except Exception as e:
            send_slack_message(channel_id, f"❌ GPT error parsing due date: {str(e)}")
            conversation_states.pop(user_id, None)
            return jsonify({"ok": True})

        # Simple format check
        if len(due_date) != 10 or "-" not in due_date:
            send_slack_message(channel_id, "⚠️ Couldn't understand the due date. Please say something like 'tomorrow' or 'July 2, 2025'.")
            conversation_states.pop(user_id, None)
            return jsonify({"ok": True})

        # 🧱 Build Jira payload
        jira_payload = {
            "fields": {
                "project": {"key": "BT"},
                "summary": convo["summary"],
                "duedate": due_date,
                "priority": {"name": convo["priority"]},
                "issuetype": {"name": "Task"}
            }
        }

        # 📩 Send to Jira
        jira_resp = requests.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue",
            headers=get_jira_auth_header(),
            json=jira_payload
        )

        # ✅ Response
        if jira_resp.status_code == 201:
            issue_key = jira_resp.json().get("key")
            send_slack_message(channel_id, f"✅ Created Jira issue *{issue_key}*: {convo['summary']}")
        else:
            send_slack_message(channel_id, f"❌ Failed to create Jira issue.\n{jira_resp.text}")

        conversation_states.pop(user_id, None)

    return jsonify({"ok": True})

# 🚀 Start the server
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
