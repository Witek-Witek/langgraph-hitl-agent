# Stage 11 — tożsamość i własność: JWT (Supabase Auth) + rejestr wątków

## Co wprowadza
Każde żądanie niesie tożsamość (JWT z Supabase Auth, weryfikowany lokalnie przez JWKS).
Każdy wątek ma właściciela. Lista wątków przechodzi z localStorage do tabeli `threads`.

## Model zagrożeń (§0 SPEC)
Klient dostaje klucz publiczny Supabase. Tabele w `public` byłyby wtedy osiągalne przez
Data API obok backendu. Obrona dwuwarstwowa:
1. **Data API wyłączone** (Project Settings → Data API) — `/rest/v1/` nie wystawia schematów.
2. **RLS włączone bez polityk** na wszystkich tabelach `public` — odmowa dla `anon`/`authenticated`.

T-1 przed/po: RLS było już włączone na tabelach checkpointera, a Data API wyłączone
(`PGRST002`, `db-schemas=pg_pgrst_no_exposed_schemas`) — ekspozycja z §0 nie występowała.
Migracja i tak zapisuje stan jawnie.

**Ostrzeżenie advisora `rls_enabled_no_policy` jest ZAMIERZONE.** Nie dodawać polityk,
nie używać `FORCE ROW LEVEL SECURITY` — backend łączy się jako właściciel tabel.

## Uruchomienie
    uv sync
    # SQL Editor w Supabase: sql/001_threads_and_rls.sql (idempotentne)
    uv run python diagnostics.py      # auth_jwks, db_threads_table, db_rls_public
    uv run python serve.py
    uv run python run.py              # CLI bez auth, wątek "cli"
    uv run langgraph dev              # Studio bez auth

Konta: Supabase → Authentication → Users → Add user (Auto confirm). Rejestracja poza zakresem.

## Przepływ wątku
    POST /threads            → serwer generuje thread_id (UUID), wpis w `threads`
    POST /chat/stream        → tożsamość → własność (rejestr) → guard stanu → touch → graf
    klucz checkpointu        = scoped_thread_id(user_id, thread_id) = "<user_id>:<thread_id>"
Klient zna tylko `thread_id`. Klucz checkpointu nie wychodzi poza serwer.

## Endpointy
- `POST/GET /threads`, `PATCH/DELETE /threads/{id}` — rejestr (cudzy/nieistniejący → 404)
- `POST /chat/stream`, `POST /threads/{id}/resume`, `POST /threads/{id}/finish`
- `GET /threads/{id}/state`, `GET /threads/{id}/history` (z `args` narzędzi — dług T-6 zamknięty)
- `GET /config/options` — wymaga tokenu
- `GET /health` — jedyny bez tokenu

## Uwagi
- **401 vs 503:** zły token → 401; JWKS niedostępny / bez kluczy → 503 (klient nie wylogowuje).
- **leeway=30 s** w weryfikacji — tolerancja rozjazdu zegarów (L-17). Rozjazd > 30 s = synchronizuj czas.
- **E-4:** weryfikacja lokalna nie widzi sesji unieważnionych w panelu — token ważny do wygaśnięcia (~1 h).
- **Klient:** token doklejany w `api/auth.js` (jedyny `fetch`); 401 → refresh → 1 ponowienie → wylogowanie.
  Drzewo aplikacji kluczowane `user.id` — zmiana użytkownika niszczy cały stan.
- **Usunięty wątek:** checkpointy zostają jako osierocone, ale niedostępne (retencja poza zakresem).
- **Dług:** balanced (DeepSeek przez OpenRouter) bywa zwracany z tokenami narzędzi jako tekst (L-19).