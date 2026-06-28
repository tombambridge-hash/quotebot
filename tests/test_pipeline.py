import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot import main as main_mod
from quotebot.easypv import EasyPVError


def _cfg(tmp_path):
    return {
        "_config_path": str(tmp_path / "config.yaml"),
        "icloud": {"email": "tombambridge@icloud.com", "app_password": "x"},
        "owner": {"notify_email": "tombambridge@icloud.com",
                  "command_senders": ["tombambridge@icloud.com"]},
        "leads": {}, "anthropic": {"enabled": True, "api_key": "k"},
        "easypv": {"enabled": True}, "pricing": {"panel_wattage_kw": 0.44},
        "bot": {"state_db": "state.db", "log_dir": str(tmp_path / "logs")},
    }


@dataclass
class FakeMail:
    uid: int
    sender: str
    subject: str
    body: str
    is_lead: bool = False
    is_command: bool = False


def _bot(tmp_path, monkeypatch, sent):
    bot = main_mod.Bot(_cfg(tmp_path))
    monkeypatch.setattr(main_mod.notify, "notify_owner",
                        lambda cfg, subj, body: sent.append((subj, body)))
    monkeypatch.setattr(main_mod.notify, "send_email",
                        lambda cfg, to, subj, body: sent.append((subj, body)))
    return bot


LEAD_BODY = ("name\nRoss Michael\nemail\nross@example.com\n"
             "address\n12 Walden Grange Close, Newport, NP19 8AZ\n")


def test_full_pipeline_success(tmp_path, monkeypatch):
    sent = []
    bot = _bot(tmp_path, monkeypatch, sent)
    monkeypatch.setattr(bot.provider, "create_project", lambda lead: "PROJ1")
    monkeypatch.setattr(bot.provider, "generate_proposal", lambda pid, lead: "DONE")
    monkeypatch.setattr(bot.provider, "wait_for_proposal", lambda pid: True)

    bot.handle_lead(FakeMail(1, "noreply@formspree.io", "New lead", LEAD_BODY,
                             is_lead=True))

    row = bot.store.get_lead(1)
    assert row["status"] == "proposal_generated"
    assert row["proposal_confirmed"] == 1
    assert row["easypv_project_id"] == "PROJ1"
    assert any("processed" in s.lower() for s, _ in sent)
    assert any("easy-pv.co.uk/project/PROJ1" in b for _, b in sent)


def test_pipeline_computer_use_failure_emails_manual(tmp_path, monkeypatch):
    sent = []
    bot = _bot(tmp_path, monkeypatch, sent)
    monkeypatch.setattr(bot.provider, "create_project", lambda lead: "PROJ2")

    def boom(pid, lead):
        raise EasyPVError("step cap hit")
    monkeypatch.setattr(bot.provider, "generate_proposal", boom)

    bot.handle_lead(FakeMail(2, "noreply@formspree.io", "New lead", LEAD_BODY,
                             is_lead=True))

    row = bot.store.get_lead(1)
    assert row["status"] == "needs_manual"
    assert row["easypv_project_id"] == "PROJ2"
    body = "\n".join(b for _, b in sent)
    assert "complete this proposal manually" in body
    assert "easy-pv.co.uk/project/PROJ2" in body


def test_pipeline_proposal_not_confirmed_falls_back(tmp_path, monkeypatch):
    sent = []
    bot = _bot(tmp_path, monkeypatch, sent)
    monkeypatch.setattr(bot.provider, "create_project", lambda lead: "PROJ3")
    monkeypatch.setattr(bot.provider, "generate_proposal", lambda pid, lead: "DONE")
    monkeypatch.setattr(bot.provider, "wait_for_proposal", lambda pid: False)

    bot.handle_lead(FakeMail(3, "noreply@formspree.io", "New lead", LEAD_BODY,
                             is_lead=True))
    assert bot.store.get_lead(1)["status"] == "needs_manual"


def test_lead_without_address_is_awaiting(tmp_path, monkeypatch):
    sent = []
    bot = _bot(tmp_path, monkeypatch, sent)
    bot.handle_lead(FakeMail(4, "noreply@formspree.io", "New lead",
                             "name\nBob\nemail\nbob@example.com\n", is_lead=True))
    assert bot.store.get_lead(1)["status"] == "awaiting_info"


def test_uid_recorded_before_processing(tmp_path, monkeypatch):
    """A crash mid-pipeline must still advance last_uid so the email isn't
    reprocessed on restart."""
    sent = []
    bot = _bot(tmp_path, monkeypatch, sent)
    bot.store.set_meta("last_uid", "0")

    def explode(msg):
        raise RuntimeError("crash mid-processing")
    monkeypatch.setattr(bot, "handle_lead", explode)
    monkeypatch.setattr(bot.mailbox, "fetch_new",
                        lambda last_uid: [FakeMail(77, "noreply@formspree.io",
                                                   "x", LEAD_BODY, is_lead=True)])
    bot.tick()  # handler raises, but tick swallows it
    assert bot.store.get_meta("last_uid") == "77"
