import logging
import pickle
import os
from time import time
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep

from src.settings import Settings

settings = Settings()

logger = logging.getLogger(__name__)


class Authenticator:
    """Class that handles the authentication to the CROUS website and returns a WebDriver object that is authenticated."""

    def __init__(self, email: str, password: str, delay: int = 2):
        self.email = email
        self.password = password
        self.delay = delay
        self.cookies_file = "cookies.pkl"

    def authenticate_driver(self, driver: WebDriver) -> None:
        """Authenticates the given WebDriver object to the CROUS website."""
        
        # Try to load cookies first
        if self._load_cookies(driver):
            logger.info("Cookies loaded. Checking session validity...")
            # Check if session is valid by visiting a protected page
            driver.get("https://trouverunlogement.lescrous.fr/tools/36/search")
            sleep(2)
            
            # Check for "Identification" button or "Se connecter" which means logged out
            is_logged_out = False
            try:
                if len(driver.find_elements(By.XPATH, "//*[contains(text(), 'Identification') or contains(text(), 'Se connecter')]")) > 0:
                    is_logged_out = True
            except:
                pass

            if not is_logged_out and "login" not in driver.current_url and "connect" not in driver.current_url:
                logger.info("Session is valid! Skipping login.")
                return
            else:
                logger.info("Session might be expired. Attempting to restore...")
                # Prevent stale cookies from being reused on next runs.
                self._delete_cookies_file()

        # If we are here, either cookies failed or session looks invalid.
        # BUT maybe we just need to hit the "Se connecter" button to trigger SSO with existing cookies?
        # Let's try to hit the connect endpoint which often revives the session via SSO if cookies are partially valid
        try:
             driver.get("https://trouverunlogement.lescrous.fr/mse/discovery/connect")
             sleep(2)
             if "login" not in driver.current_url and "connect" not in driver.current_url:
                  # It worked!
                  logger.info("Session restored via SSO refresh.")
                  return
        except:
             pass
        
        # If still not good, perform full login. 
        # Crucial: Clear potentially corrupted cookies before fresh login
        logger.info("Session invalid. Clearing cookies and performing full login flow...")
        try:
            driver.delete_all_cookies()
        except:
            pass
            
        self._perform_full_login(driver)
        
        # Save new cookies
        self._save_cookies(driver)

    def _perform_full_login(self, driver: WebDriver) -> None:
        wait = WebDriverWait(driver, 10) # 10 seconds timeout

        logger.info("Authenticating to the CROUS website...")

        # Step 1: Go to the login page

        logger.info(f"Going to the login page: {settings.MSE_LOGIN_URL}")
        driver.get(settings.MSE_LOGIN_URL)
        
        # Step 2: choose the correct authentication method
        logger.info("Choosing the correct authentication method")
        try:
            mse_connect_button = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "loginapp-button")))
        except:
            # Fallback for new page structure
            mse_connect_button = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src*='MSEConnect']")))
            # Get parent element (likely the link/button)
            mse_connect_button = mse_connect_button.find_element(By.XPATH, "./..")

        # Simulate a click
        driver.execute_script("arguments[0].click();", mse_connect_button)
        
        # Step 3: Input credentials and submit
        logger.info("Inputting credentials")
        
        # Wait for either old or new username field pattern to appear avoiding timeout
        username_input = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[name='j_username'], input[name='login[login]']")
        ))

        # Identify which one was found to get the corresponding password field
        if username_input.get_attribute("name") == "j_username":
            password_input = driver.find_element(By.NAME, "j_password")
        else:
            password_input = driver.find_element(By.NAME, "login[password]")

        username_input.send_keys(self.email)
        password_input.send_keys(self.password)

        # Handle Altcha / Captcha if present
        logger.info("Checking for captcha...")
        try:
            # Look for checkbox with name or id containing 'altcha'
            altcha_checkbox = driver.find_element(By.CSS_SELECTOR, "input[name*='altcha'], input[id*='altcha']")
            logger.info("Captcha found! Attempting to solve...")
            
            # Click the checkbox (use JS to be safe as it might be covered or styled)
            driver.execute_script("arguments[0].click();", altcha_checkbox)
            
            # Wait for captcha verification
            sleep(3) 
            logger.info("Captcha clicked.")
        except:
            logger.info("No captcha found or unable to click it.")

        logger.info("Submitting the form")
        try:
             # Try finding the designated login button if one exists explicitly
            submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            driver.execute_script("arguments[0].click();", submit_button)
        except:
             # Fallback to hitting RETURN on the password field
            password_input.send_keys(Keys.RETURN)

        # Handle potentially blocking alerts (e.g. "Vérification en cours...")
        try:
            WebDriverWait(driver, 3).until(EC.alert_is_present())
            alert = driver.switch_to.alert
            logger.info(f"Alert found and handled: {alert.text}")
            alert.accept()
        except:
            # No alert found, proceed normally
            pass
            
        # Also wait for URL change or body load to ensure login is processed
        try:
            # Wait for something that indicates we left the login page
            wait.until(lambda d: "login" not in d.current_url or len(d.window_handles) > 0)
        except:
            pass

        # Step 4: Force update the auth status
        logger.info("Connecting via SSO to CROUS Accommodation...")
        try:
            driver.get("https://trouverunlogement.lescrous.fr/mse/discovery/connect")
            
            # Wait for SSO redirect to complete
            sleep(3)
            
            # If we are still not logged in (check for "Se connecter" or "Identification"), try to click it
            # This handles cases where auto-sso didn't trigger but we have the session
            try:
                login_btn = driver.find_elements(By.XPATH, "//*[contains(text(), 'Identification') or contains(text(), 'Se connecter')]")
                if len(login_btn) > 0:
                     logger.info("Still seeing login button, clicking it to force SSO...")
                     driver.execute_script("arguments[0].click();", login_btn[0])
                     sleep(3)
            except:
                pass

        except Exception as e:
            # Sometimes an alert pops up exactly when we try to navigate
            logger.warning(f"Error navigating after login: {e}")
            try:
                alert = driver.switch_to.alert
                alert.accept()
                driver.get("https://trouverunlogement.lescrous.fr/mse/discovery/connect")
            except:
                pass


        # Done
        logger.info("Successfully authenticated to the CROUS website")
        
    def _save_cookies(self, driver: WebDriver):
        try:
            with open(self.cookies_file, "wb") as cookies_output:
                pickle.dump(driver.get_cookies(), cookies_output)
            logger.info("Cookies saved successfully.")
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")

    def _load_cookies(self, driver: WebDriver) -> bool:
        if not os.path.exists(self.cookies_file):
            return False
            
        try:
            # Need to be on the domain to set cookies
            driver.get("https://trouverunlogement.lescrous.fr/404") # Random page on domain
            
            with open(self.cookies_file, "rb") as cookies_input:
                cookies = pickle.load(cookies_input)

            if not isinstance(cookies, list):
                logger.warning("Cookie file format is invalid. Deleting it.")
                self._delete_cookies_file()
                return False

            now = int(time())
            expired_seen = False
            loaded_count = 0

            for cookie in cookies:
                if not isinstance(cookie, dict):
                    continue

                expiry = cookie.get("expiry")
                if expiry is not None:
                    expiry = int(expiry)
                    if expiry <= now:
                        expired_seen = True
                        continue
                    cookie["expiry"] = expiry

                try:
                    driver.add_cookie(cookie)
                    loaded_count += 1
                except Exception as add_error:
                    # Ignore per-cookie add errors and continue with the rest.
                    logger.debug(f"Skipping invalid cookie entry: {add_error}")

            if loaded_count == 0:
                logger.info("No usable cookies found. Removing cookie file.")
                self._delete_cookies_file()
                return False

            if expired_seen:
                logger.info("Expired cookies were detected and ignored.")

            return True
        except Exception as e:
            logger.warning(f"Failed to load cookies: {e}")
            self._delete_cookies_file()
            return False

    def _delete_cookies_file(self) -> None:
        try:
            if os.path.exists(self.cookies_file):
                os.remove(self.cookies_file)
                logger.info("Removed stale cookie file.")
        except Exception as e:
            logger.warning(f"Could not remove cookie file: {e}")
