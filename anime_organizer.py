# anime_organizer.py — Главная точка входа для организации аниме
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "scripts"))

# Import and run organizer from scripts/
from organizer import run_organizer

if __name__ == '__main__':
    run_organizer()
