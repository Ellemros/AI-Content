def test_dashboard_and_health(db):
    db.commit()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        overview = client.get("/")
        assert overview.status_code == 200
        assert "AI Content Factory" in overview.text
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/topics").status_code == 200
        assert client.get("/analytics").status_code == 200
        production = client.get("/production")
        assert production.status_code == 200
        assert "stops" in production.text.lower() or "approval" in production.text.lower()
        assert client.get("/library").status_code == 200
        created = client.post(
            "/topics",
            data={
                "title": "A careful question",
                "description": "Limits first.",
                "category": "science",
                "target_language": "en",
                "target_platform": "youtube_shorts",
                "target_audience": "curious adults",
                "priority": "3",
                "estimated_content_potential": "6",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        page = client.get("/topics")
        assert "A careful question" in page.text
