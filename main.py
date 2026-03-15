import argparse
import logging
import time
import re
import sys
import os
from typing import List, Optional

import telepot
import telepot.loop
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromiumService
from selenium.webdriver.chrome.webdriver import WebDriver
try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

from chromedriver_py import binary_path

from src.authenticator import Authenticator
from src.parser import Parser
from src.models import UserConf
from src.notification_builder import NotificationBuilder
from src.settings import Settings
from src.telegram_notifier import TelegramNotifier

# Configure logging
log_handlers = [logging.StreamHandler(sys.stdout)]
if getattr(sys, 'frozen', False):
    # If running as exe, log to file
    log_handlers.append(logging.FileHandler("crous_bot.log", encoding='utf-8'))

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%m/%d/%Y %I:%M:%S %p",
    level=logging.INFO,
    handlers=log_handlers
)
logger = logging.getLogger("accommodation_notifier")


def load_users_conf(settings: Settings) -> List[UserConf]:
    return [
        UserConf(
            conf_title="Me",
            telegram_id=settings.MY_TELEGRAM_ID,
            search_url=settings.SEARCH_URL,
            ignored_ids=[2755],
        )
    ]


def create_driver(headless: bool = True) -> WebDriver:
    """Creates a configured Chrome WebDriver instance."""
    chrome_options = Options()
    
    # Common options to reduce detection
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    if headless:
        logger.info("Running in headless mode (optimized)")
        # Use new headless mode which is more accurate
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        # Set a real window size, otherwise some elements might not spawn
        chrome_options.add_argument("--window-size=1920,1080")
    else:
        logger.info("Running in non-headless mode")
        chrome_options.add_argument("--start-maximized")

    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    
    # Determine executable path: Prefer webdriver-manager (for Docker/latest Chrome), fallback to bundled binary
    executable_path = binary_path
    if ChromeDriverManager:
        try:
            logger.info("Using webdriver-manager to get matching driver...")
            executable_path = ChromeDriverManager().install()
        except Exception as e:
            logger.warning(f"Failed to use webdriver-manager ({e}), falling back to bundled binary.")
    
    driver = webdriver.Chrome(
        options=chrome_options,
        service=ChromiumService(executable_path=executable_path),
    )
    
    # Additional property removal for stealth
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver


def run_search_process(
    settings: Settings,
    bot: telepot.Bot,
    headless: bool = True,
    requester_id: str | int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    status_msg_id: int | None = None,
    only_new: bool = False,
    seen_ids: set[int] | None = None,
):
    """
    Runs the full search process: setup driver, authenticate, parse, notify.
    If requester_id is provided, sends result to that ID and respects 'notify_when_no_results=True'.
    """
    driver = None
    try:
        driver = create_driver(headless=headless)
        Authenticator(settings.MSE_EMAIL, settings.MSE_PASSWORD).authenticate_driver(driver)

        parser = Parser(driver)
        
        # If launched manually via Request, we want to know even if empty
        notify_empty = (requester_id is not None)
        notification_builder = NotificationBuilder(notify_when_no_results=notify_empty)
        
        notifier = TelegramNotifier(bot)
        user_confs = load_users_conf(settings)

        for conf in user_confs:
            logger.info(f"Handling configuration : {conf}")

            # Define callback for progress reporting (only if we have a msg ID to edit)
            def on_progress(current: int, total: int):
                if requester_id and status_msg_id:
                    try:
                        # Simple progress bar logic
                        percentage = int((current / total) * 100)
                        bar_len = 10
                        filled = int((percentage / 100) * bar_len)
                        bar = "█" * filled + "░" * (bar_len - filled)
                        
                        bot.editMessageText(
                            (requester_id, status_msg_id),
                            f"🔍 Analyse en cours... ({current}/{total})\n[{bar}] {percentage}%",
                        )
                    except Exception as e:
                        logger.warning(f"Could not update status message: {e}")

            # Pass dynamic dates if provided
            search_results = parser.get_accommodations(
                conf.search_url, date_from, date_to, status_callback=on_progress
            )

            if seen_ids is not None:
                current_ids = {
                    accommodation.id
                    for accommodation in search_results.accommodations
                    if accommodation.id is not None
                }

                if only_new:
                    new_accommodations = [
                        accommodation
                        for accommodation in search_results.accommodations
                        if accommodation.id is None or accommodation.id not in seen_ids
                    ]
                    search_results.accommodations = new_accommodations
                    search_results.count = len(new_accommodations)

                seen_ids.update(current_ids)

            notification = notification_builder.search_results_notification(search_results)
            
            # Determine target for notification
            target_id = requester_id if requester_id else conf.telegram_id
            
            if notification:
                notifier.send_notification(str(target_id), notification)
            elif notify_empty and requester_id:
                  bot.sendMessage(target_id, "ℹ️ Aucun résultat trouvé (et aucun message généré par le builder).")

    except Exception as e:
        logger.error(f"Error during search process: {e}", exc_info=True)
        if requester_id:
            try:
                bot.sendMessage(requester_id, f"❌ Erreur durant la recherche : {str(e)}")
            except Exception as notify_error:
                logger.warning(f"Failed to send error message to Telegram: {notify_error}")
    finally:
        if driver:
            driver.quit()


def convert_date_to_iso(d_str: str) -> str:
    """Convert DD-MM-YYYY or DD/MM/YYYY to YYYY-MM-DD"""
    parts = re.split(r"[-/]", d_str)
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return d_str


class BotState:
    def __init__(self):
        self.last_check = 0.0
        self.interval = 15 * 60  # 15 minutes default
        self.active = True  # Monitoring active by default
        self.seen_accommodation_ids: set[int] = set()
        self.notify_only_new_auto = True


def start_bot(settings: Settings, bot: telepot.Bot, headless: bool = True):
    """Starts the bot listener loop."""
    
    state = BotState()
    
    def handle_message(msg):
        content_type, chat_type, chat_id = telepot.glance(msg)
        text = msg.get('text', '').strip()
        logger.info(f"Received message from {chat_id}: {text}")

        # Command /start
        if text == "/start":
            state.active = True
            bot.sendMessage(chat_id, "Bonjour ! Le bot est *ACTIF* ✅\n\nCommandes disponibles :\n"
                                     "📅 *Dates* : Envoyez `JJ-MM-AAAA` pour vérifier une période.\n"
                                     "🚀 */check* : Lancer une vérification immédiate.\n"
                                     "ℹ️ */status* : Voir l'état du bot.\n"
                                     "⏱️ */interval <minutes>* : Changer la fréquence (ex: /interval 30).\n"
                                     "🔔 */newonly on|off* : Notifier seulement les nouveaux logements ou tous.\n"
                                     "🛑 */stop* : Mettre en pause la surveillance.\n"
                                     "📝 */logs* : Recevoir le fichier de logs.",
                                     parse_mode='Markdown')
            return

        # Command /stop
        if text == "/stop":
            state.active = False
            bot.sendMessage(chat_id, "🛑 Surveillance mise en pause. Envoyez /start pour reprendre.")
            return

        # Command /check (manual check)
        if text == "/check":
            text = "go" # Treat as "go" command below

        # Command /status
        if text == "/status":
            import datetime
            status_emoji = "✅ ACTIF" if state.active else "⏸️ EN PAUSE"
            last_check_str = datetime.datetime.fromtimestamp(state.last_check).strftime('%H:%M:%S') if state.last_check > 0 else "Jamais"
            notif_mode = "Nouveaux uniquement" if state.notify_only_new_auto else "Tous les résultats"
            
            if state.active:
                next_check_ts = state.last_check + state.interval
                now = time.time()
                remaining = int((next_check_ts - now) / 60) if state.last_check > 0 and next_check_ts > now else 0
                if remaining < 0: remaining = 0
                next_check_str = datetime.datetime.fromtimestamp(next_check_ts).strftime('%H:%M:%S') if state.last_check > 0 else "Immédiat"
                msg = (f"Statut : {status_emoji}\n\n"
                       f"🕒 Dernière vérif : {last_check_str}\n"
                       f"🔜 Prochaine : {next_check_str} (dans ~{remaining} min)\n"
                       f"⏱️ Intervalle : {int(state.interval / 60)} minutes\n"
                       f"🔔 Notifications auto : {notif_mode}")
            else:
                msg = (f"Statut : {status_emoji}\n\n"
                       f"🕒 Dernière vérif : {last_check_str}\n"
                       f"⏱️ Intervalle : {int(state.interval / 60)} minutes\n"
                       f"🔔 Notifications auto : {notif_mode}\n"
                       "Envoyez /start pour réactiver.")

            bot.sendMessage(chat_id, msg, parse_mode='Markdown')
            return

        # Command /newonly on|off
        if text.startswith("/newonly"):
            parts = text.split()
            if len(parts) != 2:
                bot.sendMessage(chat_id, "❌ Usage : /newonly on|off")
                return

            value = parts[1].lower()
            if value in {"on", "oui", "true", "1"}:
                state.notify_only_new_auto = True
                bot.sendMessage(chat_id, "✅ Mode activé : notifications auto uniquement pour les nouveaux logements.")
            elif value in {"off", "non", "false", "0"}:
                state.notify_only_new_auto = False
                bot.sendMessage(chat_id, "✅ Mode désactivé : notifications auto pour tous les logements trouvés.")
            else:
                bot.sendMessage(chat_id, "❌ Valeur invalide. Utilisez : /newonly on|off")
            return

        # Command /logs
        if text == "/logs":
            log_file = "crous_bot.log"
            if os.path.exists(log_file):
                try:
                    bot.sendDocument(chat_id, open(log_file, 'rb'))
                except Exception as e:
                    bot.sendMessage(chat_id, f"❌ Erreur lors de l'envoi du log : {e}")
            else:
                bot.sendMessage(chat_id, "❌ Aucun fichier de log trouvé.")
            return

        # Command /interval
        if text.startswith("/interval"):
            try:
                parts = text.split()
                if len(parts) == 2:
                    minutes = int(parts[1])
                    if minutes < 1:
                        bot.sendMessage(chat_id, "❌ L'intervalle doit être d'au moins 1 minute.")
                    else:
                        state.interval = minutes * 60
                        bot.sendMessage(chat_id, f"✅ Intervalle modifié à {minutes} minutes.")
                        logger.info(f"Interval changed to {minutes} minutes by user.")
                else:
                    bot.sendMessage(chat_id, "❌ Usage : /interval <minutes> (ex: /interval 30)")
            except ValueError:
                bot.sendMessage(chat_id, "❌ Veuillez entrer un nombre valide.")
            return

        # Try to extract dates from message or handle "Go"
        date_pattern = r"(\d{2}[-/]\d{2}[-/]\d{4})"
        dates = re.findall(date_pattern, text)
        
        d_from = None
        d_to = None
        
        msg_reply = "🕵️ Recherche en cours avec les dates par défaut..."
        is_date_search = False
        
        if len(dates) >= 2:
            d_from = convert_date_to_iso(dates[0])
            d_to = convert_date_to_iso(dates[1])
            msg_reply = f"🕵️ Recherche pour la période du {dates[0]} au {dates[1]}..."
            is_date_search = True
        elif len(dates) == 1:
            d_from = convert_date_to_iso(dates[0])
            msg_reply = f"🕵️ Recherche à partir du {dates[0]}..."
            is_date_search = True
        elif text.lower() == "go":
            msg_reply = "🕵️ Recherche immédiate (dates par défaut)..."
            is_date_search = True
        
        # If text is random and not a command, ignore unless it looks like a request
        if not is_date_search:
             # bot.sendMessage(chat_id, "Je n'ai pas compris. Envoyez /start pour l'aide.")
             return

        sent_msg = bot.sendMessage(chat_id, f"{msg_reply} (Initialisation...)")
        
        # Run search in blocking way
        run_search_process(
            settings, 
            bot, 
            headless=headless, 
            requester_id=chat_id,
            date_from=d_from,
            date_to=d_to,
            status_msg_id=sent_msg['message_id']
        )
        
        bot.sendMessage(chat_id, "✅ Recherche terminée.")

    logger.info("🤖 Bot mode activated. Send a message to the bot to trigger a search.")
    telepot.loop.MessageLoop(bot, handle_message).run_as_thread()

    try:
        while True:
            current_time = time.time()
            if state.active and current_time - state.last_check > state.interval:
                logger.info("🕒 Lancement de la vérification automatique périodique...")
                try:
                    run_search_process(
                        settings,
                        bot,
                        headless=headless,
                        only_new=state.notify_only_new_auto,
                        seen_ids=state.seen_accommodation_ids,
                    )
                except Exception as e:
                    logger.error(f"Erreur lors de la vérification automatique: {e}")
                
                state.last_check = time.time()
                logger.info(f"Prochaine vérification dans {int(state.interval / 60)} minutes.")
            
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Stopping bot listener.")


if __name__ == "__main__":
    is_frozen = getattr(sys, "frozen", False)
    
    arg_parser = argparse.ArgumentParser(
        description="Run the script in headless mode or listen for Telegram commands."
    )
    arg_parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run the script without headless mode",
    )
    arg_parser.add_argument(
        "--listen",
        action="store_true",
        default=is_frozen,  # Default to True if running as exe
        help="Run in bot mode: listen for Telegram messages instead of running once.",
    )

    args = arg_parser.parse_args()
    
    # Force headless if frozen (to be truly background) unless overridden
    # However user might want to see browser?
    # Usually background implies headless.
    headless = not args.no_headless

    try:
        # If frozen, sys.executable is the exe path.
        # Ensure .env is loaded correctly relative to exe if needed?
        # Settings already uses .env file relative to CWD.
        # If user double clicks exe, CWD is exe dir.
        settings = Settings()
    except Exception as e:
        # If Settings fails (e.g. missing .env), we can't log properly yet.
        # But we can try to log to a file
        logging.basicConfig(filename="crous_error.log", level=logging.ERROR)
        logging.error(f"Failed to load settings: {e}")
        exit(1)

    bot = telepot.Bot(token=settings.TELEGRAM_BOT_TOKEN)
    
    try:
        bot_info = bot.getMe()
        logger.info(f"Bot initialized: {bot_info.get('username')}")
    except Exception as e:
        logger.error(f"Failed to connect bot: {e}")
        exit(1)

    if args.listen:
        start_bot(settings, bot, headless=headless)
    else:
        # Run once immediately
        run_search_process(settings, bot, headless=headless)
