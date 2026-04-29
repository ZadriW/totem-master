"""Ponto de entrada WSGI para PaaS (ex.: Railway) que usam ``gunicorn main:app`` por padrão."""

from app import app

__all__ = ["app"]
