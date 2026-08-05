"""
=============================================================================
  tests/test_flow.py
  Fluxo de persistência: cidade, status de contato e sync com o Supabase.

  Roda contra um SQLite temporário e NUNCA toca a rede — as credenciais
  reais do .env são zeradas e qualquer chamada HTTP estoura o teste.

  Rode com:  python tests/test_flow.py
=============================================================================
"""
from __future__ import annotations
import sys
import types
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import prospect.config as cfg

_TMPDIR = tempfile.TemporaryDirectory(prefix="prospect_test_")
TMP = Path(_TMPDIR.name)
cfg.DB_PATH = TMP / "test_leads.db"

import prospect.db as db
db.DB_PATH = cfg.DB_PATH

from prospect import supabase_sync
from prospect.models import Lead, LeadStatus, ContactStatus
from prospect.whatsapp_sender import should_retry_restricted_lead


# ── Blindagem de rede ──────────────────────────────────────────────────────
# Sem isso o teste gravaria leads falsos na tabela de produção quando o
# .env estiver configurado (já aconteceu uma vez).
_REAL_URL, _REAL_KEY = supabase_sync.SUPABASE_URL, supabase_sync.SUPABASE_KEY
supabase_sync.SUPABASE_URL = ""
supabase_sync.SUPABASE_KEY = ""


def _sem_rede(*args, **kwargs):
    raise AssertionError("chamada de rede real durante o teste!")


supabase_sync.requests = types.SimpleNamespace(
    post=_sem_rede, get=_sem_rede, RequestException=Exception
)


falhas: list[str] = []


def check(label: str, obtido, esperado) -> None:
    ok = obtido == esperado
    if not ok:
        falhas.append(label)
    marca = "OK  " if ok else "FALHA"
    extra = "" if ok else f"  (esperado {esperado!r})"
    print(f"{marca} {label}: {obtido!r}{extra}")


def main() -> int:
    print("=== 1. init_db + upsert com inferência de cidade ===")
    db.init_db()

    db.upsert_lead(Lead(
        username="pt_curitiba",
        full_name="Joao Silva",
        bio="Personal trainer CREF 1234 | Atendo em Curitiba - PR",
        followers=5000,
        whatsapp="5541999998888",
        status=LeadStatus.WHATSAPP_FOUND,
        score=70,
    ), sync=False)

    salvo = db.get_lead("pt_curitiba")
    check("cidade inferida", (salvo.city, salvo.state), ("Curitiba", "PR"))
    check("contact_status inicial", salvo.contact_status, ContactStatus.NOT_CONTACTED)
    check("was_contacted", salvo.was_contacted, False)
    check("location_label", salvo.location_label, "Curitiba/PR")
    check("synced_at vazio", salvo.synced_at, "")

    print("\n=== 2. lead sem cidade identificável ===")
    db.upsert_lead(Lead(
        username="sem_cidade", full_name="Rita Mesquita", bio="Rita Mesquita",
        whatsapp="5511911112222", status=LeadStatus.WHATSAPP_FOUND, score=40,
    ), sync=False)
    check("cidade vazia", db.get_lead("sem_cidade").city, "")

    print("\n=== 3. status de contato ===")
    db.set_contact_status("pt_curitiba", ContactStatus.CONTACTED,
                          channel="whatsapp", increment_attempts=True, sync=False)
    salvo = db.get_lead("pt_curitiba")
    check("contact_status", salvo.contact_status, ContactStatus.CONTACTED)
    check("canal", salvo.contact_channel, "whatsapp")
    check("tentativas", salvo.contact_attempts, 1)
    check("contacted_at preenchido", bool(salvo.contacted_at), True)
    check("was_contacted", salvo.was_contacted, True)

    print("\n=== 4. re-scrape NÃO apaga histórico de contato ===")
    db.upsert_lead(Lead(
        username="pt_curitiba", full_name="Joao Silva",
        bio="Personal trainer CREF 1234 | Atendo em Curitiba - PR",
        followers=5200, status=LeadStatus.WHATSAPP_FOUND, score=72,
    ), sync=False)
    salvo = db.get_lead("pt_curitiba")
    check("contact_status preservado", salvo.contact_status, ContactStatus.CONTACTED)
    check("contacted_at preservado", bool(salvo.contacted_at), True)
    check("tentativas preservadas", salvo.contact_attempts, 1)
    check("whatsapp preservado", salvo.whatsapp, "5541999998888")
    check("followers atualizado", salvo.followers, 5200)

    print("\n=== 5. fila de disparo e filtro por cidade ===")
    check("pendentes (todas)",
          [l.username for l in db.get_pending_contact_leads()], ["sem_cidade"])
    check("pendentes em Curitiba",
          [l.username for l in db.get_pending_contact_leads(city="Curitiba")], [])
    check("leads de Curitiba",
          [l.username for l in db.get_leads_by_city("Curitiba")], ["pt_curitiba"])
    check("leads sem cidade",
          [l.username for l in db.get_leads_by_city("")], ["sem_cidade"])
    check("list_cities", db.list_cities(), ["Curitiba"])

    print("\n=== 6. resumo por cidade ===")
    resumo = {r["city"]: r for r in db.get_city_summary()}
    check("Curitiba total", resumo["Curitiba"]["total"], 1)
    check("Curitiba contatados", resumo["Curitiba"]["contatados"], 1)
    check("Curitiba pendentes", resumo["Curitiba"]["pendentes"], 0)
    check("sem cidade pendentes", resumo[""]["pendentes"], 1)

    print("\n=== 7. funil de contato ===")
    check("funil", db.get_contact_funnel(), {"contacted": 1, "not_contacted": 1})

    print("\n=== 8. Supabase não configurado degrada sem quebrar ===")
    check("credenciais reais existem no .env", bool(_REAL_URL and _REAL_KEY), True)
    check("is_configured (forçado vazio)", supabase_sync.is_configured(), False)
    ok, msg = supabase_sync.check_connection()
    check("check_connection", ok, False)
    print(f"     mensagem: {msg}")
    sincronizados, erro = supabase_sync.push_leads([db.get_lead("pt_curitiba")])
    check("push sem config não sincroniza", sincronizados, [])
    check("push sem config retorna erro", bool(erro), True)
    n, restantes, erro = db.sync_pending_leads(on_status=lambda m: None)
    check("sync_pending sem config", (n, bool(erro)), (0, True))

    print("\n=== 9. formato da requisição ao Supabase (mock) ===")
    capturado: dict = {}

    class FakeResponse:
        status_code = 201
        text = ""
        headers = {"content-range": "0-0/2"}

        def json(self):
            return []

    def fake_post(url, params=None, json=None, headers=None, timeout=None):
        capturado.update(url=url, params=params, body=json, headers=headers)
        return FakeResponse()

    supabase_sync.SUPABASE_URL = "https://demo.supabase.co"
    supabase_sync.SUPABASE_KEY = "service-role-key"
    supabase_sync.requests = types.SimpleNamespace(
        post=fake_post, get=fake_post, RequestException=Exception
    )

    sincronizados, erro = supabase_sync.push_leads([db.get_lead("pt_curitiba")])
    check("push com config sincroniza", sincronizados, ["pt_curitiba"])
    check("sem erro", erro, "")
    check("url", capturado["url"], "https://demo.supabase.co/rest/v1/leads")
    check("on_conflict", capturado["params"], {"on_conflict": "username"})
    check("Prefer upsert", capturado["headers"]["Prefer"],
          "resolution=merge-duplicates,return=minimal")
    check("Authorization", capturado["headers"]["Authorization"],
          "Bearer service-role-key")

    linha = capturado["body"][0]
    check("row username", linha["username"], "pt_curitiba")
    check("row city", linha["city"], "Curitiba")
    check("row state", linha["state"], "PR")
    check("contact_status é string", linha["contact_status"], "contacted")
    check("status é string", linha["status"], "whatsapp_found")
    check("is_verified é bool", isinstance(linha["is_verified"], bool), True)
    check("contacted_at preenchido", bool(linha["contacted_at"]), True)
    check("synced_at não vai no payload", "synced_at" in linha, False)

    # Coluna timestamptz nunca pode receber string vazia
    pendente = supabase_sync.lead_to_row(db.get_lead("sem_cidade"))
    check("contacted_at vazio -> None", pendente["contacted_at"], None)

    print("\n=== 10. mark_synced / get_unsynced_leads ===")
    db.mark_synced(["pt_curitiba"])
    check("não sincronizados",
          [l.username for l in db.get_unsynced_leads()], ["sem_cidade"])
    db.set_contact_status("pt_curitiba", ContactStatus.RESPONDED, sync=False)
    check("mudar status marca como pendente",
          sorted(l.username for l in db.get_unsynced_leads()),
          ["pt_curitiba", "sem_cidade"])

    print("\n=== 11. set_contact_status reflete o sync no retorno ===")
    atualizado = db.set_contact_status("pt_curitiba", ContactStatus.CONTACTED)
    check("synced_at no objeto devolvido", bool(atualizado.synced_at), True)
    check("igual ao que está no banco",
          db.get_lead("pt_curitiba").synced_at, atualizado.synced_at)

    print("\n=== 12. carência de lead restrito pelo WhatsApp ===")
    recente = Lead(username="a", contact_status=ContactStatus.RESTRICTED,
                   contacted_at=datetime.now().isoformat())
    pode, motivo = should_retry_restricted_lead(recente)
    check("restrito recente não retenta", pode, False)

    antigo = Lead(username="b", contact_status=ContactStatus.RESTRICTED,
                  contacted_at=(datetime.now() - timedelta(hours=30)).isoformat())
    check("restrito antigo retenta", should_retry_restricted_lead(antigo)[0], True)

    legado = Lead(username="c", contact_status=ContactStatus.RESTRICTED,
                  notes="Tentado contato em 2020-01-01 10:00, conta restrita")
    check("formato legado em notes", should_retry_restricted_lead(legado)[0], True)

    normal = Lead(username="d", contact_status=ContactStatus.NOT_CONTACTED)
    check("lead normal sempre pode", should_retry_restricted_lead(normal)[0], True)

    print("\n=== 13. exports ===")
    caminho = db.export_csv(TMP / "todos.csv")
    cabecalho = caminho.read_text(encoding="utf-8").splitlines()[0]
    check("CSV tem coluna Cidade", "Cidade" in cabecalho, True)
    check("CSV tem coluna Status Contato", "Status Contato" in cabecalho, True)
    _, total = db.export_city_csv("Curitiba", TMP / "curitiba.csv")
    check("export por cidade", total, 1)

    print("\n=== 14. apply_remote_contact_status ===")
    supabase_sync.pull_contact_status = lambda on_status=None: ([{
        "username": "sem_cidade", "contact_status": "converted",
        "contact_channel": "whatsapp", "contacted_at": "2026-08-01T10:00:00",
        "contact_attempts": 2, "last_contact_error": "",
        "city": "São Paulo", "state": "SP",
    }], "")
    atualizados, erro = db.apply_remote_contact_status(on_status=lambda m: None)
    check("atualizados do remoto", atualizados, 1)
    salvo = db.get_lead("sem_cidade")
    check("status vindo do remoto", salvo.contact_status, ContactStatus.CONVERTED)
    check("cidade vinda do remoto", (salvo.city, salvo.state), ("São Paulo", "SP"))

    print("\n" + "=" * 60)
    if falhas:
        print(f"❌ {len(falhas)} falha(s): {falhas}")
        return 1
    print("✅ Todos os testes passaram")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        _TMPDIR.cleanup()
    sys.exit(codigo)
