"""One brand. English is the channel. Czech is a language, not a second channel."""

BRAND_NAME = "AI & Future"
BRAND_DESCRIPTION = (
    "English-first explainers on AI, tools, and future technology. "
    "Czech is a separate localized version, not a translated duplicate. "
    "One English YouTube destination. Nothing is uploaded automatically."
)

AUDIENCE = {
    "ai": "students, professionals, and founders learning practical AI",
    "technology": "people comparing AI tools and how they change work",
    "science": "readers who want the study rather than the headline",
    "future": "people separating a forecast from a shipping product",
    "facts": "viewers who want one sourced claim and its limit",
}

# Public feeds that responded when this list was chosen. Disable any of them in the dashboard.
DEFAULT_FEEDS = (
    ("arXiv cs.AI", "https://export.arxiv.org/rss/cs.AI", "science"),
    ("Google AI blog", "https://blog.google/innovation-and-ai/technology/ai/rss/", "ai"),
    ("OpenAI News", "https://openai.com/news/rss.xml", "ai"),
)
