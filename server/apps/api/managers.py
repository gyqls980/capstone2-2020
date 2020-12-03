from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.hashers import make_password
from django.utils.translation import ugettext_lazy as _

class CustomUserManager(BaseUserManager):
    """
    Custom user model manager where user_id is the unique identifiers.
    """
    def create(self, user_id, password, **extra_fields):
        return self.create_user(user_id, password, **extra_fields)
    
    def create_user(self, user_id, password, **extra_fields):
        """
        Create and save a User with the given user_id and password
        """
        user = self.model(user_id=user_id, **extra_fields)
        user.set_password(password)
        user.save()
        return user
    
    def create_superuser(self, user_id, password, **extra_fields):
        """
        Create and save a SuperUser with the given user_id and password.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))
        
        return self.create_user(user_id, password, **extra_fields)
