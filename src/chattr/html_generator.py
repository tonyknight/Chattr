from pathlib import Path

from jinja2 import Environment, FileSystemLoader
import os
import datetime

def to_view(row):
    """A template-ready dict from a database row (sqlite3.Row) or a parser message dict.

    Database rows name the sender `message_sender` and the text `body`; the template uses
    `author` and `message`. Rows are read-only, so a new dict is built.
    """
    data = dict(row)
    timestamp = data.get("timestamp")
    if isinstance(timestamp, str):
        timestamp = datetime.datetime.fromisoformat(timestamp)
    return {
        **data,
        "timestamp": timestamp,
        "author": data.get("message_sender") or data.get("author") or data.get("message_author"),
        "message": data.get("body") if data.get("body") is not None else data.get("message", ""),
        "type": data.get("type") or "text",
    }


class HTMLGenerator:
    """Handles the generation of the final HTML report."""
    def __init__(self, template_dir=None):
        template_dir = template_dir or str(Path(__file__).resolve().parent / "templates")
        self.env = Environment(loader=FileSystemLoader(template_dir))
        self.template = self.env.get_template("template.html")

    def generate_html(self, messages, output_path, main_author=None):
        """
        Renders the HTML file from a list of processed message dictionaries.
        
        Args:
            messages (list): The list of message dictionaries.
            output_path (str): The full path where the HTML file will be saved.
            main_author (str, optional): The name of the main user/owner. Defaults to None.
        """
        if not messages:
            print("No messages to generate HTML for.")
            return

        # Simplified data preparation for now. Will be expanded.
        # This part will need to be made more robust to handle the full calendar, etc.
        messages = [to_view(m) for m in messages]
        messages_by_date = {}
        for msg in messages:
            date = msg["timestamp"].date()
            if date not in messages_by_date:
                messages_by_date[date] = []
            messages_by_date[date].append(msg)
            
        # For now, we'll pass an empty calendar and a placeholder main_author
        # This will be replaced with a more robust data preparation step.
        calendar_data = [] # Placeholder
        if not main_author and messages:
            # A simple heuristic to find the main author for non-iMessage chats
            all_authors = list(dict.fromkeys(m["author"] for m in messages if m["author"]))
            if not all_authors:
                main_author = "Me"
            else:
                main_author = all_authors[1] if len(all_authors) > 1 else all_authors[0]


        html_content = self.template.render(
            messages_by_date=messages_by_date,
            main_author=main_author,
            calendar_data_by_year=calendar_data # Placeholder
        )
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        print(f"HTML file generated at: {output_path}")
