import logging
import os
import re
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

# --- Load known lists from files ---

def load_list_from_file(filename):
    try:
        with open(filename, encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.error(f"File not found: {filename}")
        return []

KNOWN_CUSTOMERS = load_list_from_file("known_customers.txt")
KNOWN_PRODUCTS = load_list_from_file("known_products.txt")

# --- Google Sheets Setup ---

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

    # Step 1: Try basic regex
    match = re.match(r"(.+?)\s*\.\s*(\d+)(კგ|ც|ლ|გრამი)?\s+(.+?)(?:[,;]\s*(.*))?$", line)
    if match:
        customer_raw, number, unit, product_raw, comment = match.groups()
        comment = comment or ""
    else:
        # fallback if regex fails
        logging.warning(f"Regex did not match for line: {line}")
        return call_gpt_fallback(line)

    # Step 2: Fuzzy match
    matched_customer = fuzzy_match(customer_raw, KNOWN_CUSTOMERS)
    matched_product = fuzzy_match(product_raw, KNOWN_PRODUCTS)

    customer = matched_customer if matched_customer else customer_raw
    product = matched_product if matched_product else product_raw

    return {
        "type": "order",
        "customer": customer,
        "product": product,
        "amount_value": number,
        "amount_unit": unit or "",
        "comment": comment,
        "raw_customer": customer_raw,
        "raw_product": product_raw,
        "customer_unknown": matched_customer is None,
        "product_unknown": matched_product is None
    }
def call_gpt_fallback(text):
    logging.warning(f"Using GPT fallback for: {text}")
    try:
        customer_str = ", ".join(KNOWN_CUSTOMERS)
        product_str = ", ".join(KNOWN_PRODUCTS)

        prompt = f"""
        Given the Georgian text order, extract the following:
        - Customer (must match these): {customer_str}
        - Product (must match these): {product_str}
        - Amount value and unit
        - Comment if available

        Format response as JSON:
        {{
            "customer": "XXX",
            "product": "YYY",
            "amount_value": "ZZ",
            "amount_unit": "კგ|ც|ლ|გრამი",
            "comment": "..."
        }}

        If matching fails, return raw values from the message.
        Text: "{text}"
        """

        response = client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts structured order data."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content.strip()
        parsed = json.loads(content)

        parsed["type"] = "order"
        parsed["raw_customer"] = parsed.get("customer", "")
        parsed["raw_product"] = parsed.get("product", "")
        parsed["customer_unknown"] = parsed["customer"] not in KNOWN_CUSTOMERS
        parsed["product_unknown"] = parsed["product"] not in KNOWN_PRODUCTS

        return parsed

    except Exception as e:
        logging.error(f"GPT parsing failed: {e}")
        return {
            "type": "order",
            "customer": text,
            "product": "",
            "amount_value": "?",
            "amount_unit": "",
            "comment": "",
            "raw_customer": text,
            "raw_product": "",
            "customer_unknown": True,
            "product_unknown": True
        }

def update_google_sheet(data, author):
    if data['type'] == 'order':
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            str(timestamp).strip(),
            str(data['customer']).strip(),
            str(data['product']).strip(),
            str(data['amount_value']).strip(),
            str(data['amount_unit']).strip(),
            str(data['comment']).strip(),
            str(author).strip()
        ]
        try:
            sheet.append_rows([[
                timestamp.strip(),
                data['customer'].strip(),
                data['product'].strip(),
                data['amount_value'].strip(),
                data['amount_unit'].strip(),
                data['comment'].strip(),
                author.strip()
            ]], value_input_option='USER_ENTERED')
        except Exception as e:
            logging.error(f"Failed to write to sheet: {e}")

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
                    if data['customer_unknown']:
                        warn += " ⚠️ უცნობი მომხმარებელი"
                    if data['product_unknown']:
                        warn += " ⚠️ უცნობი პროდუქტი"
                    await update.message.reply_text(
                        f"✅ Logged: {data['raw_customer']} / {data['raw_product']} / {data['amount_value']} / {data['amount_unit']}"
                        + (" ⚠️ უცნობი მომხმარებელი" if data['customer_unknown'] else "")
                        + (" ⚠️ უცნობი პროდუქტი" if data['product_unknown'] else "")
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
