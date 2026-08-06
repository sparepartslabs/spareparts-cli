from spareparts.modules.lgtm.diff import (
    changed_lines,
    is_grounded,
    parse_diff,
    render_for_prompt,
)

DIFF = """diff --git a/src/charge.ts b/src/charge.ts
index 1111111..2222222 100644
--- a/src/charge.ts
+++ b/src/charge.ts
@@ -10,6 +10,9 @@ export function charge(cents: number) {
   if (cents <= 0) return;
+  if (cents > MAX) {
+    throw new Error('too much');
+  }
   post(cents);
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-old
+new
"""


def test_parses_files_and_hunks():
    files = parse_diff(DIFF)
    assert [f.path for f in files] == ["src/charge.ts", "README.md"]
    assert len(files[0].hunks) == 1
    assert files[0].hunks[0].header.startswith("@@ -10,6 +10,9 @@")


def test_skips_the_minus_plus_header_lines():
    # `--- a/x` and `+++ b/x` sit before the first hunk and must not be counted
    # as removed and added lines.
    files = parse_diff(DIFF)
    assert changed_lines(files[1]) == 2


def test_counts_only_changed_lines():
    files = parse_diff(DIFF)
    assert changed_lines(files[0]) == 3


def test_grounding_ignores_the_section_heading():
    files = parse_diff(DIFF)
    assert is_grounded(files, "src/charge.ts", "@@ -10,6 +10,9 @@")
    assert is_grounded(files, "src/charge.ts", "@@ -10,6 +10,9 @@ something else")


def test_grounding_rejects_invented_citations():
    files = parse_diff(DIFF)
    assert not is_grounded(files, "src/nope.ts", "@@ -10,6 +10,9 @@")
    assert not is_grounded(files, "src/charge.ts", "@@ -99,1 +99,1 @@")
    assert not is_grounded(files, "src/charge.ts", "not a hunk header")


def test_render_drops_whole_files_rather_than_truncating():
    files = parse_diff(DIFF)
    text, included = render_for_prompt(files, 200)
    assert len(included) < len(files)
    # Whatever survived is complete: every included path is fully present.
    for file in included:
        assert f"### {file.path}" in text


def test_render_prefers_the_most_changed_file():
    files = parse_diff(DIFF)
    _, included = render_for_prompt(files, 200)
    assert included[0].path == "src/charge.ts"
