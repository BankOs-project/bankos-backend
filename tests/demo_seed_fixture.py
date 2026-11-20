async def load_demo_seed() -> None:
    from app.core.database import async_session_factory
    from scripts.seed_demo import seed

    async with async_session_factory() as session:
        await seed(session)
