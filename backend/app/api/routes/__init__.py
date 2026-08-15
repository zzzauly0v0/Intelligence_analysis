"""API Routes.

This module contains the API routes for the application.
Any new version of the API routes should be added here.
"""

from app.api.routes.v1 import v1_router

__all__ = ["v1_router"]
