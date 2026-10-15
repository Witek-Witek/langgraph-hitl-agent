def scoped_thread_id(user_id: str, thread_id: str) -> str:
    """JEDYNE miejsce konstrukcji klucza checkpointu. Klient zna wyłącznie thread_id."""
    return f"{user_id}:{thread_id}"
