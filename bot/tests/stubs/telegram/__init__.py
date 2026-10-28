"""Stub minimal de python-telegram-bot, utilisé UNIQUEMENT si la vraie bibliothèque n'est pas installée
(permet de vérifier que bot/app.py s'importe et s'assemble). Avec le vrai paquet, les tests l'utilisent."""
class BotCommand:
    def __init__(self, command, description): self.command, self.description = command, description
class InlineKeyboardButton:
    def __init__(self, text, callback_data=None): self.text, self.callback_data = text, callback_data
class InlineKeyboardMarkup:
    def __init__(self, rows): self.inline_keyboard = rows
class KeyboardButton:
    def __init__(self, text): self.text = text
class ReplyKeyboardMarkup:
    def __init__(self, rows, **kw): self.keyboard = rows
class Update: pass
