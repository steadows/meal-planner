import re
from collections.abc import Collection, Mapping, Sequence
from datetime import date

from pydantic import TypeAdapter, ValidationError

from meals.contracts import TRUSTED, MealieSlug, MealieUnavailable, RecipeOption


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


_SLUG = TypeAdapter(MealieSlug)


def _require_slug(slug: str) -> str:
    """The real client treats anything outside Mealie's slug charset as unknown."""
    try:
        return _SLUG.validate_python(slug)
    except ValidationError as exc:
        raise ValueError(f"FakeMealieClient: {slug!r} isn't a slug Mealie would issue") from exc


class FakeMealieClient:
    """In-memory Mealie. `importable` maps URLs the fake can scrape to the recipe they produce.
    `get_recipe` raises MealieUnavailable for a slug in `unavailable`, as for a recipe Mealie errors on.
    """

    def __init__(
        self,
        recipes: Mapping[str, RecipeOption] | None = None,
        tags: Mapping[str, Sequence[str]] | None = None,
        importable: Mapping[str, RecipeOption] | None = None,
        unavailable: Collection[str] = (),
    ) -> None:
        self.recipes: dict[str, RecipeOption] = {
            _require_slug(slug): recipe for slug, recipe in (recipes or {}).items()
        }
        self._tags = {tag: tuple(slugs) for tag, slugs in (tags or {}).items()}
        self._importable = dict(importable or {})
        self._unavailable = frozenset(map(_require_slug, unavailable))
        self.meal_plans: dict[date, tuple[str, ...]] = {}

    def import_url(self, url: str) -> str:
        if url not in self._importable:
            raise ValueError(f"FakeMealieClient: can't scrape {url}")
        recipe = self._importable[url]
        slug = _require_slug(_slugify(recipe.name))
        self.recipes[slug] = recipe
        return slug

    def get_recipe(self, slug: str) -> RecipeOption:
        if slug in self._unavailable:
            raise MealieUnavailable(f"FakeMealieClient: {slug} is set to fail")
        # The idiom the real client uses: validated under TRUSTED, so the slug charset holds too.
        fields = self.recipes[slug].model_dump() | {"mealie_slug": slug}
        return RecipeOption.model_validate(fields, context={TRUSTED: True})

    def list_by_tag(self, tag: str) -> tuple[str, ...]:
        return self._tags.get(tag, ())

    def set_meal_plan(self, week_start: date, slugs: Sequence[str]) -> str:
        unknown = [slug for slug in slugs if slug not in self.recipes]
        if unknown:
            raise KeyError(f"FakeMealieClient: unknown recipes {unknown}")
        self.meal_plans[week_start] = tuple(slugs)
        return f"fake-plan-{week_start.isoformat()}"
