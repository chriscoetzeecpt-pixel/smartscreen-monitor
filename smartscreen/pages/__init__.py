"""Page registry. Add a page: create a module with a Page subclass and register it here."""
from .metrics import MetricsPage
from .weather import WeatherPage
from .posts import PostsPage

PAGES = {
    "metrics": MetricsPage,
    "weather": WeatherPage,
    "posts": PostsPage,
}
