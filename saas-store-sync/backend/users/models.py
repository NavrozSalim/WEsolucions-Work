import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Optional: we can require email as the username field
    # USERNAME_FIELD = 'email'
    # REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email
