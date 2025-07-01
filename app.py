from flask import Flask, request, jsonify
import os, time
import requests
from dotenv import load_dotenv
from base64 import b64encode
from openai import OpenAI
import dateparser

# 🌱 Load environment variables from .env
load_dotenv()

# 🌐 Flask App
app = Flask(__name__)

# ✅ Keep track of handled Slack event IDs to prevent duplicate processing
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

# ✅ Send a message back to Slack
def send_slack_message(channel_id, message):
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel_id, "text": message}
    )

# 🧠 Conversational state cache
conversation_states = {}

# 📥 Slack Events Endpoint
@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    # ✅ Handle Slack's URL verification
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]}), 200, {'Content-Type': 'application/json'}

    # 🔐 Slack Event Deduplication
    event_id = data.get("event_id")
    if event_id in handled_event_ids:
        return jsonify({"ok": True})
    handled_event_ids.add(event_id)

    event = data.get("event", {})
    user_msg = event.get("text", "").strip()
    user_id = event.get("user")
    channel_id = event.get("channel")

    # 🛑 Skip if message is from a bot or empty
    if not user_msg or "bot_id" in event:
        return jsonify({"ok": True})

    # 🤖 Multi-step conversation tracking
    convo = conversation_states.get(user_id, {})

    if not convo:
        conversation_states[user_id] = {"step": "ask_summary"}
        send_slack_message(channel_id, "📝 What is the task summary?")
    elif convo["step"] == "ask_summary":
        convo["summary"] = user_msg
        convo["step"] = "ask_due"
        send_slack_message(channel_id, "📅 When is it due?")
    elif convo["step"] == "ask_due":
        convo["due_raw"] = user_msg
        convo["step"] = "ask_priority"
        send_slack_message(channel_id, "❗ How important is it? (e.g., Low, Medium, High)")
    elif convo["step"] == "ask_priority":
        convo["priority"] = user_msg.capitalize()
        convo["step"] = "create_issue"

        # 📅 Parse due date
        parsed_due = dateparser.parse(convo["due_raw"])
        due_date = parsed_due.date().isoformat() if parsed_due else None

        if not due_date:
            send_slack_message(channel_id, "⚠️ Couldn't understand the due date. Please restart by sending a new message.")
            conversation_states.pop(user_id, None)
            return jsonify({"ok": True})

        # 🧱 Create Jira issue payload
        jira_payload = {
            "fields": {
                "project": {"key": "BT"},
                "summary": convo["summary"],
                "duedate": due_date,
                "priority": {"name": convo["priority"]},
                "issuetype": {"name": "Task"}
            }
        }

        jira_resp = requests.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue",
            headers=get_jira_auth_header(),
            json=jira_payload
        )

        if jira_resp.status_code == 201:
            issue_key = jira_resp.json().get("key")
            send_slack_message(channel_id, f"✅ Created Jira issue *{issue_key}*: {convo['summary']}")
        else:
            send_slack_message(channel_id, f"❌ Failed to create Jira issue.\n{jira_resp.text}")

        conversation_states.pop(user_id, None)

    return jsonify({"ok": True})

# 🚀 Start Flask
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
