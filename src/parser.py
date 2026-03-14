import logging
import re
import datetime
from time import sleep
from typing import List, Optional, Callable

from bs4 import BeautifulSoup
from pydantic import HttpUrl
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By 
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from src.models import Accommodation, SearchResults
from src.settings import Settings

settings = Settings()

logger = logging.getLogger(__name__)


class Parser:
    """Class to parse the CROUS website and get the available accommodations"""

    def __init__(self, authenticated_driver: WebDriver):
        self.driver = authenticated_driver

    def get_accommodations(
        self,
        search_url: HttpUrl,
        date_from: str | None = None,
        date_to: str | None = None,
        status_callback: Callable[[int, int], None] | None = None,
    ) -> SearchResults:
        """Returns the accommodations found on the CROUS website for the given search URL"""
        wait = WebDriverWait(self.driver, 10)
        logger.info(f"Getting accommodations from the search URL: {search_url}")
        self.driver.get(str(search_url))
        
        # Wait for either cards or "Aucun"
        try:
             wait.until(lambda d: d.find_elements(By.CLASS_NAME, "fr-card") or "Aucun" in d.page_source)
        except:
             pass

        html = self.driver.page_source
        search_results_soup = BeautifulSoup(html, "html.parser")
        
        # Parse basic info from list
        accommodations = parse_accommodations_summaries(search_results_soup)
        num_accommodations = len(accommodations)
        logger.info(f"Found {num_accommodations} accommodations in list")
        
        # Enrich with details if needed (e.g. date check)
        detailed_accommodations = []
        for index, acc in enumerate(accommodations):
            if status_callback:
                status_callback(index + 1, num_accommodations)

            try:
                # Visit details page
                details_url = f"https://trouverunlogement.lescrous.fr/tools/{self._extract_tool_id(str(search_url))}/accommodations/{acc.id}"
                self.driver.get(details_url)
                
                # Check if already in selection (means available/selected)
                try:
                    # Look for "Retirer de ma sélection" button or text
                    if len(self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Retirer de ma sélection')]")) > 0:
                         logger.info(f"Accommodation {acc.id} is already in selection!")
                         acc.date = "✅ DÉJÀ SÉLECTIONNÉ (Pas de vérif date nécessaire)"
                         detailed_accommodations.append(acc)
                         continue
                except:
                    pass

                try:
                    # Wait for inputs to be present
                    wait.until(EC.presence_of_element_located((By.NAME, "arrivalDate")))
                    arrival_input = self.driver.find_element(By.NAME, "arrivalDate")
                    departure_input = self.driver.find_element(By.NAME, "departureDate")
                    
                    # Fill dates from settings
                    today_str = datetime.date.today().strftime("%Y-%m-%d")
                    target_date = date_from if date_from else (settings.DATE_FROM if settings.DATE_FROM else today_str)
                    
                    default_end = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
                    target_end_date = date_to if date_to else (settings.DATE_TO if settings.DATE_TO else default_end)
                    
                    logger.info(f"Checking availability for {acc.id} from {target_date} to {target_end_date}")
                    
                    def fill_date_input(element, date_str):
                        # date_str expected as YYYY-MM-DD
                        element.click()
                        sleep(0.1)
                        # Clear strictly
                        element.send_keys(Keys.CONTROL, "a")
                        element.send_keys(Keys.BACKSPACE)
                        
                        # Reformating date YYYY-MM-DD -> DDMMYYYY for typing
                        parts = date_str.split("-")
                        if len(parts) == 3:
                            formatted = f"{parts[2]}{parts[1]}{parts[0]}"
                            element.send_keys(formatted)
                        else:
                            element.send_keys(date_str)
                        
                        element.send_keys(Keys.TAB)

                    fill_date_input(arrival_input, target_date)
                    sleep(0.2)
                    fill_date_input(departure_input, target_end_date)
                    sleep(0.2)

                    # Click "Vérifier les disponibilités"
                    check_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'disponibilit')]")))
                    self.driver.execute_script("arguments[0].click();", check_btn)
                    
                    # Wait for popup/result
                    sleep(2) # Give it a moment to appear
                    
                    popup_content = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".fr-modal__content, .fr-alert, [role='dialog']")))
                    popup_text = popup_content.text
                    
                    logger.info(f"Availability check result for {acc.id}: {popup_text[:100]}...")
                    
                    text_lower = popup_text.lower()
                    
                    # Analyze Popup Content
                    is_available = False
                    
                    # Strong negative indicators (override everything else)
                    if "désolé" in text_lower or "aucun" in text_lower or "pas disponible" in text_lower:
                         is_available = False
                    # Strong positive indicators
                    elif "périodes disponibles" in text_lower or "réserver" in text_lower or "choisir cette période" in text_lower:
                         is_available = True
                    # Fallback: Check for date ranges (e.g. "Du 01/01/26 au ...")
                    elif re.search(r"du\s*\d{1,2}/\d{1,2}", text_lower):
                         is_available = True
                    
                    if is_available:
                         logger.info(f"Accommodation {acc.id} IS available!")
                         
                         acc.date = f"✅ DISPONIBLE du {target_date} au {target_end_date}" 
                         
                         # If the popup gives specific ranges (e.g. "Du 01/09/24 au ..."), append that info
                         dates_found = re.findall(r"(\d{1,2}/\d{1,2}/\d{2,4})", popup_text)
                         if len(dates_found) >= 2:
                             acc.date += f" (Période proposée : {dates_found[0]} -> {dates_found[1]})"

                         detailed_accommodations.append(acc)
                         continue
                    else:
                         logger.info(f"Accommodation {acc.id} is NOT available for these dates.")
                         acc.date = f"❌ NON DISPONIBLE du {target_date} au {target_end_date}"
                         detailed_accommodations.append(acc)
                         continue

                except Exception as ex:
                    logger.warning(f"Could not check availability form for {acc.id}: {ex}")
                    # If form check failed but we are in date filtering mode,
                    # we might want to still add it but mark as unchecked
                    if date_from or settings.DATE_FROM:
                         acc.date = "❓ Vérification échouée (Site instable ?)"
                         detailed_accommodations.append(acc)
                         continue

                # Extract date from static text as fallback
                if not acc.date:
                     availability_date = self._extract_availability_date()
                     if availability_date:
                        acc.date = f"Disponible à partir du {availability_date}"
                
                # If we get here, it means we didn't do the explicit form check
                detailed_accommodations.append(acc)
                
            except Exception as e:
                logger.error(f"Failed to get details for {acc.id}: {e}")
                if acc not in detailed_accommodations:
                    detailed_accommodations.append(acc) # Keep it even if details fail
            
        return SearchResults(
            search_url=search_url,
            count=len(detailed_accommodations),
            accommodations=detailed_accommodations,
        )

    def _extract_tool_id(self, url: str) -> str:
        match = re.search(r"/tools/(\d+)/", url)
        return match.group(1) if match else "36" # Default to 36

    def _extract_availability_date(self) -> str | None:
        # Try to find date in the page content
        page_text = self.driver.find_element(By.TAG_NAME, "body").text
        
        # Patterns like "Libre au 01/09/2026", "Disponible à partir du ..."
        # Regex to find date in DD/MM/YYYY format
        date_pattern = r"(?:libre|disponible|à partir)\s+(?:au|du|dès|le)?\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})"
        match = re.search(date_pattern, page_text, re.IGNORECASE)
        if match:
            return match.group(1)
            
        return None

    def _get_accomodations_count(
        self, search_results_soup: BeautifulSoup
    ) -> Optional[int]:
        results_heading = search_results_soup.find(
            "h2", class_="SearchResults-desktop fr-h4 svelte-11sc5my"
        )

        if not results_heading:
            return None

        number_or_aucun = results_heading.text.split()[0]

        if number_or_aucun == "Aucun":
            return 0

        try:
            number = int(number_or_aucun)
            return number
        except ValueError:
            return None


def _try_parse_url(title_card) -> HttpUrl | None:
    try:
        return title_card.find("a")["href"]
    except Exception:
        return None


def _try_parse_id(url: str | None) -> int | None:
    if not url:
        return None

    try:
        return int(url.split("/")[-1])
    except Exception:
        return None


def _try_parse_image_url(image):
    if not image:
        return None
    try:
        return image["src"]
    except Exception:
        return None


def _try_parse_price(price) -> float | str | None:
    if not price:
        return None
    try:
        return float(price.text.strip().strip("€").strip().replace(",", "."))
    except Exception:
        pass

    return price.text.strip()


def parse_accommodation_card(card: BeautifulSoup) -> Accommodation | None:
    title_card = card.find("h3", class_="fr-card__title")
    if not title_card:
        return None

    title = title_card.text.strip()
    url = _try_parse_url(title_card)
    accommodation_id = _try_parse_id(str(url))

    image = card.find("img", class_="fr-responsive-img")
    image_url = _try_parse_image_url(image)

    overview_details = []

    # Add address
    address = card.find("p", class_="fr-card__desc")
    if address:
        overview_details.append(address.text.strip())

    # Add other details
    details = card.find_all("p", class_="fr-card__detail")
    for detail in details:
        overview_details.append(detail.text.strip())

    price = card.find("p", class_="fr-badge")

    price = _try_parse_price(price)

    return Accommodation(
        id=accommodation_id,
        title=title,
        image_url=image_url,  # type: ignore
        price=price,
        overview_details="\n".join(overview_details),
    )


def parse_accommodations_summaries(
    search_results_soup: BeautifulSoup,
) -> List[Accommodation]:
    cards = search_results_soup.find_all("div", class_="fr-card")

    accommodations: List[Accommodation] = []
    for card in cards:
        accommodation = parse_accommodation_card(card)
        if accommodation:
            accommodations.append(accommodation)

    return accommodations
