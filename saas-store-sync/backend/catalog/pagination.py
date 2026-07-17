from rest_framework.pagination import PageNumberPagination


class CatalogProductPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500

    def get_count(self, queryset):
        """Count without expensive annotations/subqueries on the list queryset."""
        # ``values('pk')`` drops select_related/annotations for the count plan.
        try:
            return queryset.order_by().values('pk').count()
        except Exception:
            return super().get_count(queryset)
