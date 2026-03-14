from src.models import Accommodation, Notification, SearchResults


import re

class NotificationBuilder:
    """Class that builds notifications from search results."""

    def __init__(self, notify_when_no_results: bool = False):
        self.notify_when_no_results = notify_when_no_results

    def search_results_notification(
        self, search_results: SearchResults
    ) -> Notification | None:
        accommodations = search_results.accommodations
        if not accommodations and not self.notify_when_no_results:
            return None

        if not accommodations:
            message = "Aucun logement trouvé. Voici une liste des ponts de France où vous pourriez dormir : https://fr.wikipedia.org/wiki/Liste_de_ponts_de_France"
        else:
            # Sort accommodations: Those with "✅" (available) first
            accommodations.sort(key=lambda x: "✅" not in (x.date or "z"))
            
            count = len(accommodations)
            avail_count = sum(1 for a in accommodations if a.date and "✅" in a.date)
            
            if avail_count > 0:
                header = f"🚨 {avail_count} Logements DISPONIBLES (sur {count} trouvés) :"
            else:
                header = f"ℹ️ {count} Logements trouvés (mais aucun disponible immédiatement) :"

            message = f"{header}\n\n"

        # Extract tool ID from search URL
        tool_id = "36"
        tool_match = re.search(r"/tools/(\d+)/", str(search_results.search_url))
        if tool_match:
            tool_id = tool_match.group(1)

        def escape_markdown(text: str | None) -> str:
            if not text:
                return ""
            return text.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")

        def format_one_accommodation(accommodation: Accommodation):
            price = (
                f"{accommodation.price}€"
                if isinstance(accommodation.price, float)
                else accommodation.price
            )

            link = f"https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{accommodation.id}"
            
            # Escape title for Markdown
            title = escape_markdown(accommodation.title)
            
            # Escape price just in case
            price = escape_markdown(str(price))
            
            # Add date and nice formatting
            icon = "🏠"
            status_line = ""
            
            if accommodation.date:
                if "✅" in accommodation.date:
                    icon = "⭐" # Highlight available ones
                    status_line = f"\n**{accommodation.date}**" # Make status bold if available
                else:
                    status_line = f"\n{accommodation.date}"

            return f"{icon} [{title}]({link}) ({price}){status_line}"

        message += "\n\n".join(map(format_one_accommodation, accommodations))

        # Used a named link for the search URL to avoid underscore issues
        message += f"\n\n[Lien de recherche]({search_results.search_url})"

        return Notification(message=message)
