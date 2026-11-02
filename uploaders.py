"""Shared upload step for recipe artifacts.

Used by both extraction pipelines and the approval-resume flow so target
handling (the built-in library, Tandoor, Mealie) lives in exactly one place.
"""

from __future__ import annotations

from typing import Optional

from helpers import setup_logger

logger = setup_logger('upload')

TARGET_LABELS = {'library': 'Library', 'tandoor': 'Tandoor', 'mealie': 'Mealie'}


def get_enabled_targets() -> list[str]:
    """Return the upload targets currently enabled in Settings, in order."""
    from config import config

    targets = []
    if config.LIBRARY_ENABLED:
        targets.append('library')
    if config.TANDOOR_ENABLED:
        targets.append('tandoor')
    if config.MEALIE_ENABLED:
        targets.append('mealie')
    return targets


def format_targets(targets: list[str]) -> str:
    """Human-readable label, e.g. 'Library & Mealie' or 'Mealie'."""
    labels = [TARGET_LABELS.get(t, t) for t in targets]
    if len(labels) > 1:
        return ' & '.join(labels)
    return labels[0] if labels else 'no target'


def upload_to_target(target: str, recipe_data: dict, image_path: Optional[str]) -> bool:
    """Send one recipe to one target. Raises on failure; True if the image went too."""
    if target == 'library':
        from kitchen.library import save_pipeline_recipe
        save_pipeline_recipe(recipe_data, image_path)
        return bool(image_path)
    if target == 'tandoor':
        from tandoor import Tandoor
        tandoor = Tandoor()
        result = tandoor.create_recipe(recipe_data)
        if image_path and result.get('id'):
            return bool(tandoor.upload_image(result['id'], image_path))
        return False
    if target == 'mealie':
        from mealie import Mealie
        mealie = Mealie()
        result = mealie.create_recipe(recipe_data)
        recipe_slug = result.get('slug') or result.get('id')
        if image_path and recipe_slug:
            return bool(mealie.upload_image(recipe_slug, image_path))
        return False
    raise ValueError(f'Unknown target: {target}')


def upload_recipe_to_targets(recipe_data: dict,
                             image_path: Optional[str]) -> tuple[str, list]:
    """Upload recipe (+ optional image) to all enabled targets.

    Returns (final_target_label, failures) where failures is a list of
    (target, error_message) tuples. Raises nothing for individual target
    errors; callers decide how to report them. When nothing is enabled a
    synthetic failure is returned so callers surface a clear message.
    """
    name = recipe_data.get('name') or recipe_data.get('title') or 'Untitled'
    targets = get_enabled_targets()
    if not targets:
        logger.info(
            f'[Upload] "{name}" was not saved. No destination is enabled. '
            'Turn on the Library, Mealie or Tandoor in Settings.'
        )
        return 'none', [('none',
                         'No destination is enabled — turn on the Library, '
                         'Mealie and/or Tandoor in Settings')]

    logger.info(f'[Upload] Sending "{name}" to {format_targets(targets)}.')
    results: list[tuple[str, bool, Optional[str]]] = []
    for target in targets:
        try:
            upload_to_target(target, recipe_data, image_path)
            results.append((target, True, None))
        except Exception as upload_error:
            label = TARGET_LABELS.get(target, target)
            logger.error(f'[Upload] {label} did not accept "{name}": {upload_error}')
            results.append((target, False, str(upload_error)))

    final_target = format_targets(targets)
    failed = [r for r in results if not r[1]]
    if failed and len(failed) < len(targets):
        succeeded = [r[0] for r in results if r[1]]
        final_target = format_targets(succeeded)

    if not failed:
        logger.info(f'[Upload] "{name}" is now in {final_target}.')
    return final_target, [(t, msg) for t, ok, msg in failed]
