"""Scanned repository bean for the fixture app (WI 3.6 e2e)."""

from pyleaf.data import CrudRepository

from .models import Note


class NoteRepo(CrudRepository[Note, int], bean=True):
    """``bean=True`` applies the ``@Repository`` stereotype: a package scan
    registers this class like any other component."""
