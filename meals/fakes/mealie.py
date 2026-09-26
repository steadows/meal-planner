import re
from collections.abc import Mapping, Sequence
from datetime import date

from meals.contracts import RecipeOption


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class FakeMealieClient:
    """In-memory Mealie. `importable` maps URLs the fake can scrape to the recipe they produce."""

    def __init__(
        self,
        recipes: Mapping[str, RecipeOption] | None = None,
        tags: Mapping[str, Sequence[str]] | None = None,
        importable: Mapping[str, RecipeOption] | None = None,
    ) -> None:
        self.recipes: dict[str, RecipeOption] = dict(recipes or {})
        self._tags = {tag: tuple(slugs) for tag, slugs in (tags or {}).items()}
        self._importable = dict(importable or {})
        self.meal_plans: dict[date, tuple[str, ...]] = {}

    def import_url(self, url: str) -> str:
        if url not in self._importable:
            raise ValueError(f"FakeMealieClient: can't scrape {url}")
        recipe = self._importable[url]
        slug = _slugify(recipe.name)
        self.recipes[slug] = recipe
        return slug

    def get_recipe(self, slug: str) -> RecipeOption:
        return self.recipes[slug].model_copy(update={"mealie_slug": slug})

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        return self._tags.get(tag, ())

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        unknown = [slug for slug in slugs if slug not in self.recipes]
        if unknown:
            raise KeyError(f"FakeMealieClient: unknown recipes {unknown}")
        self.meal_plans[week_start] = tuple(slugs)
        return f"fake-plan-{week_start.isoformat()}"
