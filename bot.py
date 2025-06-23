import logging
import os
import re
import json
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from rapidfuzz import process
from openai import OpenAI
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- SETUP ---

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_CREDENTIALS_FILE = "google-credentials.json"
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")

client_ai = OpenAI(api_key=OPENAI_API_KEY)

# Known lists
KNOWN_PRODUCTS = [
    "მცენარეული ცხიმი", "მაიონეზი", "ქათმის ნაგეთსი", "კარტოფილი ფრი", "ფიტურნიის ცხიმი", "პირობითი პროდუქტი", "ქათმის ფილე", "თევზი სიომგა", "პანგასიუსის ფილე", "ქათმის მკერდი", "მზესუმზირის ზეთი",
    "ღორის ბარკალი", "ორაგულის ფილე", "სკუმბრია", "საქონლის ხორცი", "საქონლის ბარამანსა", "ძროხის ხორცი", "თევზი ხეკი", "ქათამი", "ქათმის ბარკალი", "ქათმის კროკეტი", "ქათმის კუჭი", "ქათმის ფრთა", "ქათმის ღვიძლი", "ღორის სუკი", "ღორის კისერი", "ღორის ჩალაღაჯი", "ღორის ნეკნი", "ფრიტურის ზეთი", "მექსიკური კარტოფილი", "ორაგული", "კეტჩუპი", "ქათმის ინერფილე", "საქონლის ხორცის სტეიკი", "თხევადი აირი", "ღორის ხორცი", "პიცის ყველი", "თევზი ტილაპიის ფილე", "ქათმის ფარში", "ღორის კანჭი", "კარტოფილი", "ღორის ბეჭი", "ბუნებრივი აირი", "თვითწებვადი ლენტა", "სტრეჩი", "ავტონაწილები", "თევზი ორაგული", "დიზელი","ხორცის დაჭრის მომსახურება"
]

KNOWN_CUSTOMERS = [
    "ფუდსელი", "თეისთი", "დიღომი ჰუდი", "ნინო მუშკუდიანი", "ზემელი", "გიორგი რაზმაძე"
]

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
sheet = gspread.authorize(creds).open(SPREADSHEET_NAME).sheet1

# --- UTILS ---

def fuzzy_match(term, known_list, threshold=50):
    match, score, _ = process.extractOne(term, known_list)
    logging.info(f"Matching '{term}' → '{match}' (score: {score})")
    return match if score >= threshold else None

def extract_data_from_line(line):
    line = line.strip()
    match = re.match(r"(.+?)\s*\.\s*(.+?)\s+(\d+)(კგ|ც)?\s*(.*)?", line)
    if not match:
        logging.warning(f"Regex did not match for line: {line}")
        return None

    customer_raw, product_raw, number, unit, comment = match.groups()
    matched_customer = fuzzy_match(customer_raw, KNOWN_CUSTOMERS)
    matched_product = fuzzy_match(product_raw, KNOWN_PRODUCTS)

    return {
        "type": "order",
        "customer": matched_customer or customer_raw,
        "product": matched_product or product_raw,
        "amount_value": number,
        "amount_unit": unit or "",
        "comment": comment or "",
        "raw_customer": customer_raw,
        "raw_product": product_raw
    }

def update_google_sheet(data, author):
    if data['type'] == 'order':
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([
            timestamp,
            data['customer'],
            data['product'],
            data['amount_value'],
            data['amount_unit'],
            data['comment'],
            author
        ])

# --- TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Send me an order and I’ll log it to Google Sheets.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    author = update.message.from_user.full_name or update.message.from_user.username or str(update.message.from_user.id)
    lines = text.split('\n')

    for line in lines:
        for subline in re.split(r'[;,]', line):
            subline = subline.strip()
            if subline:
                data = extract_data_from_line(subline)
                if data:
                    update_google_sheet(data, author)
                    warn = ""
                    if data['customer'] == data['raw_customer']:
                        warn += " ⚠ უცნობი მომხმარებელი"
                    if data['product'] == data['raw_product']:
                        warn += " ⚠ უცნობი პროდუქტი"
                    await update.message.reply_text(
                        f"✅ Logged: {data['customer']} / {data['product']} / {data['amount_value']}{data['amount_unit']}{warn}"
                    )
                else:
                    await update.message.reply_text(f"❌ Couldn't parse: {subline}")

# --- MAIN ---

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
