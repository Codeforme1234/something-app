from app.repositories import keys


def test_question_sk_is_zero_padded_so_order_sorts_lexicographically():
    ordered = [keys.question_sk(n) for n in (1, 2, 9, 10, 99, 100)]
    assert ordered == sorted(ordered)
    assert keys.question_sk(1) == "Q#001"


def test_test_meta_lives_in_the_teacher_partition():
    # Ownership is enforced by the key: a test is only reachable via its owner.
    assert keys.teacher_pk("abc") == "TEACHER#abc"
    assert keys.test_sk("01J9") == "TEST#01J9"


def test_prefixes_do_not_collide_within_the_test_partition():
    prefixes = [
        keys.QUESTION_SK_PREFIX,
        keys.SESSION_SK_PREFIX,
        keys.SUBMISSION_SK_PREFIX,
    ]
    for a in prefixes:
        for b in prefixes:
            if a is not b:
                assert not a.startswith(b)
