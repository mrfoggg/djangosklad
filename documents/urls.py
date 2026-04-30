from django.urls import path

from .views import get_latest_price_ajax

urlpatterns = [
    path("get-price/", get_latest_price_ajax, name="get_latest_price"),
]
