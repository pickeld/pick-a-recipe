# Changelog

## [Unreleased]

### New Features

- **The Chef, a built-in recipe library**: Extracted recipes are saved to the app's own library by default (Mealie and Tandoor stay optional). Search, tag and ingredient filters, collections, favorites, ratings, private notes, multiple photos, and JSON export, print and share links.
- **Servings and units**: Scale any recipe to a different number of servings, and switch between metric and US units.
- **Nutrition**: Per-serving and whole-recipe estimates with a coverage indicator, manual overrides, and daily totals in the planner.
- **Meal planner**: Week and month views with drag and drop, leftovers, copy last week, and a private calendar (ICS) feed.
- **Shopping list**: Built from a recipe or the week's plan, merged and grouped by aisle (it learns your moves), skips what's in the pantry, syncs live, and works offline.
- **Pantry**: Staples, expiry dates, moving bought items in, and optionally deducting ingredients when you cook.
- **Sous Chef (AI)**: Finds recipes from the library for the ingredients you have, invents new ones, suggests substitutions, uses up expiring food, plans your week, reviews recipes, and suggests tags.
- **Cook mode**: Full screen, keeps the screen awake, step by step with the step's ingredients and tap-to-start timers.
- **Import**: Mealie and Tandoor (admin), Paprika exports, recipe pages, cookbook photos, PDFs and Schema.org JSON, with review and duplicate handling.
- **Cook log**: History and yearly stats.
- **Hebrew and English**: The new screens are translated, with right-to-left layout. Hebrew is the default.

## [1.4.0] - 2026-01-21

### New Features

- **History Page & Navigation**: Added History page with navigation link, job tracking, and completed results display
- **Bulk Delete Functionality**: Implemented bulk delete for history and job entries with new status indicators
- **Dual Export Support**: Added ability to export recipes to both Tandoor and Mealie simultaneously with dual export badge and preview updates
- **Export/Import Settings**: Added export/import settings functionality for easier configuration management
- **Unit & Food Management**: Implemented unit and food management in Mealie exporter with fetching and creation logic
- **Nutrition Display**: Enabled nutrition display in Mealie settings with enhanced logging for nutrition updates
- **URL Retry Functionality**: Added URL data attribute to history items for improved retry functionality
- **Source URL Handling**: Added source URL handling in recipe update payload for Mealie integration
- **Multi-architecture Docker**: Added build-and-push script for multi-architecture Docker image support

### Improvements

- **Ingredient Structure**: Updated ingredient structure to include 'notes' and 'raw' fields for improved recipe export compatibility
- **Enhanced Logging**: Improved logging for ingredient and nutrition processing across Chef, Mealie, and Tandoor exporters
- **Error Handling**: Improved error handling and logging for recipe update requests in Mealie exporter
- **History Filtering**: Exclude failed recipe history entries if a successful entry exists for the same URL
- **Code Refactoring**: Moved video URL input declaration for improved readability

### Technical Changes

- Upgraded yt-dlp on startup with modified CMD to run Flask application
- Updated main.js version to 4 for script consistency

## [1.3.0] - 2026-01-14

### New Features

- Centralized logging system across all modules for better debugging and traceability
- HuggingFace token configuration for authenticated model downloads
- Added `ingredientReferences` field to Mealie recipe instructions

### Bug Fixes

- Fixed database file path consistency with `ui/database.py`
- Updated Dockerfile to install curl and unzip dependencies
- Fixed PWA share_target to use POST method with proper enctype
