from rest_framework.pagination import PageNumberPagination


class ListingPagination(PageNumberPagination):
    """Default 10 rows per page for managed Inventory (Created products stays unpaginated)."""

    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_count(self, queryset):
        try:
            return queryset.order_by().values('pk').count()
        except Exception:
            return super().get_count(queryset)
