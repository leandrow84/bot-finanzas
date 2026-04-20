import os
import json
import base64
import re
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from anthropic import Anthropic
from datetime import datetime
import requests as http_requests

app = Flask(__name__)
anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_sheets_client():
    raw = os.environ.get("GCREDS")
    creds_data = json.loads(raw)
    creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_sheets_client()
    return client.open_by_key(os.environ.get("SPREADSHEET_ID"))

def get_local_from_number(phone_number):
    try:
        ss = get_spreadsheet()
        config = ss.worksheet("CONFIG")
        data = config.get_all_records()
        phone_clean = str(phone_number).strip().lstrip("+")
        for row in data:
            config_num = str(row["NUMERO"]).strip().lstrip("+")
            if config_num == phone_clean:
                return row["LOCAL"]
        return None
    except Exception as e:
        print(f"
