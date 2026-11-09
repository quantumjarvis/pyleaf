"""A ``@Service`` with a ``@Transactional`` write (WI 3.6 e2e)."""

from pyleaf.core import Service
from pyleaf.tx import Transactional

from .models import Note
from .repo import NoteRepo


@Service
class NoteService:
    """Writes through the injected repository inside a tx boundary."""

    def __init__(self, repo: NoteRepo) -> None:
        self._repo = repo

    @Transactional
    def create(self, text: str) -> None:
        """Persist a note; joins the (starter) transaction manager."""
        self._repo.save(Note(text=text))
