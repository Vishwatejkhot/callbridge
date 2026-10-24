"""`callbridge` command line: init, chat, serve, eval, improve, mcp."""

import argparse
import sys
import uuid


def cmd_init(args) -> None:
    from sqlalchemy import select

    from callbridge import db
    from callbridge.agent.prompts import PromptConfig
    from callbridge.db.models import PromptVersion
    from callbridge.db.seed import seed_demo

    db.init_db()
    with db.session() as s:
        if args.demo:
            clinic = seed_demo(s)
            print(f"demo clinic ready: {clinic.name} (id {clinic.id}), staff login demo@harbourside.example / demo-password")
        if not s.scalar(select(PromptVersion).where(PromptVersion.status == "live")):
            s.add(PromptVersion(version="v1", config=PromptConfig().model_dump(exclude={"version"}), status="live",
                                rationale="initial version", approved_by="bootstrap"))
            print("behaviour config v1 is live")


def cmd_chat(args) -> None:
    """Talk to the agent in the terminal, exactly as a caller would (minus the audio)."""
    from sqlalchemy import select

    from callbridge import db
    from callbridge.agent.session import CallSession
    from callbridge.db.models import Clinic
    from callbridge.improver import rollout

    with db.session() as s:
        clinic = s.scalar(select(Clinic).order_by(Clinic.id))
        if clinic is None:
            sys.exit("no clinic: run `callbridge init --demo` first")
        clinic_id = clinic.id
    call_id = f"cli-{uuid.uuid4().hex[:12]}"
    config, arm, shadow = rollout.choose(call_id)
    session = CallSession(call_id, clinic_id, args.phone, config=config, rollout_arm=arm, shadow=shadow)
    print(f"AI: {session.start()}")
    try:
        while True:
            text = input("You: ").strip()
            if not text:
                continue
            result = session.turn(text)
            print(f"AI: {result.reply}  [{result.latency_ms:.0f} ms]")
            if result.ended:
                break
        session.end("completed")
    except (EOFError, KeyboardInterrupt):
        session.end("hangup")
    print(f"\noutcome: {session.outcome()}")


def cmd_serve(args) -> None:
    import uvicorn

    uvicorn.run("callbridge.api.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_eval(args) -> None:
    from callbridge.evals import runner

    sys.exit(runner.main(args))


def cmd_generate(args) -> None:
    from callbridge.evals import generate

    generate.main(args.out, args.seed)


def cmd_improve(args) -> None:
    from callbridge.improver import loop

    sys.exit(loop.main(args))


def cmd_purge(args) -> None:
    from callbridge.jobs import purge_expired

    print(purge_expired())


def cmd_mcp(args) -> None:
    from callbridge.tools.mcp_server import main

    main()


def main(argv: list[str] | None = None) -> None:
    from callbridge.observability import setup

    setup()
    p = argparse.ArgumentParser(prog="callbridge")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create tables (and demo data)")
    sp.add_argument("--demo", action="store_true")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("chat", help="text conversation with the agent")
    sp.add_argument("--phone", default="+447700900999", help="caller id to simulate")
    sp.set_defaults(fn=cmd_chat)

    sp = sub.add_parser("serve", help="run the API + voice websocket server")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("eval", help="run the fake-patient test suite")
    sp.add_argument("--scenarios", nargs="*", default=None, help="scenario files (default: evals/scenarios/*.jsonl)")
    sp.add_argument("--filter", default=None, help="only categories/ids containing this")
    sp.add_argument("--baseline", default="evals/baseline.json")
    sp.add_argument("--update-baseline", action="store_true")
    sp.add_argument("--gate", action="store_true", help="exit 1 if the change should be blocked")
    sp.add_argument("--report", default="evals/report.json")
    sp.add_argument("--llm-patient", action="store_true", help="Claude plays the caller instead of the scripted bot")
    sp.add_argument("--verbose", "-v", action="store_true")
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("generate-scenarios", help="(re)generate the core scenario suite")
    sp.add_argument("--out", default="evals/scenarios/core.jsonl")
    sp.add_argument("--seed", type=int, default=7)
    sp.set_defaults(fn=cmd_generate)

    sp = sub.add_parser("improve", help="self-improvement loop")
    isub = sp.add_subparsers(dest="action", required=True)
    for name, helptext in [("run", "failed calls -> tests -> proposed fix -> eval gate -> shadow"),
                           ("status", "show versions and rollout state"),
                           ("evaluate", "promote / roll back based on live numbers")]:
        x = isub.add_parser(name, help=helptext)
        x.add_argument("--days", type=int, default=7)
    x = isub.add_parser("approve", help="human approval: shadow -> canary 10%%")
    x.add_argument("version")
    x.add_argument("--by", required=True)
    x.add_argument("--skip-shadow", action="store_true", help="only for the demo line, never for pilots")
    x = isub.add_parser("rollback")
    x.add_argument("version")
    sp.set_defaults(fn=cmd_improve)

    sp = sub.add_parser("purge", help="apply data retention (recordings, transcripts)")
    sp.set_defaults(fn=cmd_purge)

    sp = sub.add_parser("mcp", help="run the booking tools as an MCP server (stdio)")
    sp.set_defaults(fn=cmd_mcp)

    args = p.parse_args(argv)
    if args.cmd in ("improve", "purge"):
        from callbridge import db
        from callbridge.config import get_settings

        if get_settings().ops_database_url:
            db.configure(get_settings().ops_database_url)
    args.fn(args)


if __name__ == "__main__":
    main()
