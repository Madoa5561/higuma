from datetime import datetime, timezone

from higuma import Blob, Boolean, Database, Date, DateTime, Float, Integer, Model, String


class Artifact(Model):
    id = Integer(primary_key=True, autoincrement=True)
    name = String(length=120, nullable=False, unique=True, index=True)
    score = Float(default=0.0, nullable=False)
    published = Boolean(default=False, nullable=False)
    release_date = Date(nullable=True)
    created_at = DateTime(default=lambda: datetime.now(timezone.utc), nullable=False)
    payload = Blob(nullable=True)


def exercise_all_fields() -> dict[str, object]:
    database = Database("sqlite:///:memory:")
    database.create_all(Artifact)

    with database.session() as session:
        artifact = session.add(
            Artifact(
                name="example",
                score=9.5,
                published=True,
                release_date=datetime.now(timezone.utc).date(),
                payload=b"higuma",
            )
        )
        artifact.score = 10.0
        session.save(artifact)

        loaded = session.query(Artifact).filter_by(id=artifact.id).first()
        assert loaded is not None
        return loaded.to_dict()


if __name__ == "__main__":
    print(exercise_all_fields())
