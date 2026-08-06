from spareparts.modules.lgtm.git import parse_numstat


def test_plain_files():
    files = parse_numstat("3\t1\tsrc/a.ts\0" "10\t0\tREADME.md\0")
    assert [(f.filename, f.additions, f.deletions) for f in files] == [
        ("src/a.ts", 3, 1),
        ("README.md", 10, 0),
    ]


def test_rename_yields_the_new_path_not_the_pseudo_path():
    # Without -z this arrives as `src/{app.ts => handlers.ts}`, which is not a
    # file, and passing it to `git diff --` drops the change entirely.
    files = parse_numstat("2\t2\t\0src/app.ts\0src/handlers.ts\0")
    assert [f.filename for f in files] == ["src/handlers.ts"]


def test_rename_among_plain_files_does_not_desynchronise_the_scan():
    out = "1\t0\tsrc/a.ts\0" "2\t2\t\0src/app.ts\0src/handlers.ts\0" "4\t4\tsrc/z.ts\0"
    assert [f.filename for f in parse_numstat(out)] == [
        "src/a.ts",
        "src/handlers.ts",
        "src/z.ts",
    ]


def test_binary_files_count_as_zero_rather_than_vanishing():
    files = parse_numstat("-\t-\tlogo.png\0")
    assert [(f.filename, f.additions, f.deletions) for f in files] == [("logo.png", 0, 0)]


def test_empty_output():
    assert parse_numstat("") == []


def test_a_truncated_rename_record_does_not_loop_forever():
    assert parse_numstat("2\t2\t\0src/app.ts\0") == []
