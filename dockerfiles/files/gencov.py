#!/usr/bin/env python3
"""
Git commit-based code coverage report generator (modified lines only)

Uses existing gcov result files (.gcda, .gcno) to analyze only modified lines.

Usage:
  ./gencov.py <commit>                 # only lines modified in that commit
  ./gencov.py <commit1>..<commit2>     # only lines modified between commit1 and commit2
  ./gencov.py --all                    # full coverage (all lines)
  ./gencov.py -o outdir <commit>       # specify output directory

Options:
  -o, --output-dir DIR    output directory (default: coverage)
  -h, --help              show help

Output:
  - <output-dir>/html/           : HTML coverage report for modified lines
  - <output-dir>/untested.md     : checklist of untested code among modified lines
"""

import sys
import os
import subprocess
import re
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional


class GitDiffParser:
    """Parse git diff to extract changed line numbers"""

    @staticmethod
    def parse_diff_hunk_header(line: str) -> Optional[Tuple[int, int]]:
        """
        Parse diff hunk header: @@ -old_start,old_count +new_start,new_count @@
        Returns: (new_start, new_count) or None
        """
        match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if match:
            new_start = int(match.group(1))
            new_count = int(match.group(2)) if match.group(2) else 1
            return (new_start, new_count)
        return None

    @staticmethod
    def get_changed_lines(workspace_dir: Path, commit_range: str) -> Dict[str, Set[int]]:
        """
        Extract changed files and line numbers via git diff
        Returns: {file_path: {set of line numbers}}
        """
        if commit_range == "--all":
            return {}  # full mode does no filtering

        # Build git diff command
        if ".." in commit_range:
            # range: commit1..commit2
            git_cmd = ["git", "diff", "-U0", commit_range]
        else:
            # single commit
            git_cmd = ["git", "show", "-U0", "--format=", commit_range]

        result = subprocess.run(
            git_cmd,
            cwd=workspace_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='replace')
            print(f"❌ Git diff failed: {stderr}", file=sys.stderr)
            sys.exit(1)

        # UTF-8 decode (ignore binary files)
        stdout = result.stdout.decode('utf-8', errors='ignore')

        # Parse diff
        changed_lines = defaultdict(set)
        current_file = None

        for line in stdout.split('\n'):
            # Extract file path: diff --git a/path b/path
            if line.startswith('diff --git'):
                current_file = None  # reset when a new file starts
                match = re.search(r' b/(.+)$', line)
                if match:
                    file_path = match.group(1)
                    # only process .c and .h files
                    if file_path.endswith(('.c', '.h')):
                        abs_path = (workspace_dir / file_path).resolve()
                        if abs_path.exists():
                            current_file = str(abs_path)

            # Hunk header: @@ -old +new @@
            elif line.startswith('@@') and current_file:
                hunk_info = GitDiffParser.parse_diff_hunk_header(line)
                if hunk_info:
                    new_start, new_count = hunk_info
                    # add changed line range
                    for line_num in range(new_start, new_start + new_count):
                        changed_lines[current_file].add(line_num)

        return dict(changed_lines)


class SourceParser:
    """Parse source code to find function ranges"""

    @staticmethod
    def find_function_ranges(source_file: Path) -> Dict[str, Tuple[int, int]]:
        """
        Find function definitions and ranges in a source file
        Returns: {function_name: (start_line, end_line)}
        """
        if not source_file.exists():
            return {}

        function_ranges = {}
        try:
            with open(source_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            current_func = None
            func_start = 0
            brace_depth = 0
            in_function = False
            recent_lines = []  # store recent lines (function definitions can span multiple lines)

            for i, line in enumerate(lines, 1):
                stripped = line.strip()

                # recent line buffer (up to 10 lines)
                recent_lines.append((i, line))
                if len(recent_lines) > 10:
                    recent_lines.pop(0)

                # ignore comment blocks
                if stripped.startswith('/*') or stripped.startswith('*'):
                    continue

                # function start: found {
                if not in_function and '{' in line:
                    # find function name in recent lines
                    # pattern: function_name(params) or function_name (params)
                    # supports multi-line function declarations: match even without closing )
                    for j in range(len(recent_lines) - 1, -1, -1):
                        line_num, prev_line = recent_lines[j]
                        # find function name pattern
                        # pattern 1: complete function declaration function_name(...)
                        func_match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)', prev_line)
                        # pattern 2: multi-line function declaration function_name(..., (closing paren on next line)
                        if not func_match:
                            func_match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', prev_line)

                        if func_match:
                            func_name = func_match.group(1)
                            # exclude keywords (if, while, for, switch, etc.)
                            if func_name not in ('if', 'while', 'for', 'switch', 'catch', 'sizeof', 'typeof'):
                                current_func = func_name
                                func_start = line_num
                                brace_depth = line.count('{') - line.count('}')
                                in_function = True
                                break

                elif in_function:
                    brace_depth += line.count('{') - line.count('}')
                    if brace_depth == 0:
                        # end of function
                        if current_func:
                            function_ranges[current_func] = (func_start, i)
                        current_func = None
                        in_function = False

        except Exception as e:
            pass

        return function_ranges


class GcovParser:
    """Parse gcov output files (.gcov)"""

    @staticmethod
    def parse_gcov_line(line: str) -> Optional[Tuple[int, int]]:
        """
        Parse a gcov line: "exec_count:line_number:source_code"
        Returns: (line_number, execution_count) or None

        Format examples:
            5:  123:    printf("hello");
        #####:  124:    never_executed();
            -:  125:    // comment
        """
        # gcov format: "     count:  line: source"
        match = re.match(r'\s*([^:]+):\s*(\d+):', line)
        if match:
            count_str = match.group(1).strip()
            line_num = int(match.group(2))

            # '-' is a non-executable line, '#####' is an unexecuted line
            if count_str == '-':
                return None  # ignore non-executable lines
            elif count_str.startswith('#'):
                return (line_num, 0)  # unexecuted line
            else:
                try:
                    # strip '*' (exception handling block marker)
                    count_str_clean = count_str.rstrip('*')
                    count = int(count_str_clean)
                    return (line_num, count)
                except ValueError:
                    return None
        return None

    @staticmethod
    def run_gcov_for_file(source_file: Path, workspace_dir: Path) -> Optional[Tuple[Dict[int, int], Dict[str, Dict]]]:
        """
        Run gcov for a specific source file and extract coverage data
        Returns: (line_coverage, function_coverage)
            line_coverage: {line_number: execution_count}
            function_coverage: {function_name: {'lines_executed': int, 'lines_total': int}}
        """
        # skip .h files (header files are not compiled)
        if source_file.suffix == '.h':
            return None

        # find the .gcno file (same directory as the source file)
        gcno_file = source_file.with_suffix('.gcno')

        if not gcno_file.exists():
            # search in other locations (e.g. _srv, _shlib variants)
            source_dir = source_file.parent
            source_base = source_file.stem  # drop extension

            gcno_candidates = list(source_dir.glob(f"{source_base}*.gcno"))
            if not gcno_candidates:
                return None

            # use the most recent file
            gcno_file = max(gcno_candidates, key=lambda p: p.stat().st_mtime)

        gcno_dir = gcno_file.parent
        source_dir = source_file.parent

        # 1. per-line coverage: run gcov with default options
        result = subprocess.run(
            ["gcov", "-o", ".", source_file.name],
            cwd=source_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if result.returncode != 0:
            return None

        # parse the .gcov file
        gcov_file = source_dir / f"{source_file.name}.gcov"
        if not gcov_file.exists():
            return None

        line_coverage = {}
        try:
            with open(gcov_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    parsed = GcovParser.parse_gcov_line(line)
                    if parsed:
                        line_num, count = parsed
                        line_coverage[line_num] = count
        finally:
            if gcov_file.exists():
                gcov_file.unlink()

        # 2. per-function coverage: run gcov -f
        result_func = subprocess.run(
            ["gcov", "-f", "-o", ".", source_file.name],
            cwd=source_dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        function_coverage = {}
        if result_func.returncode == 0:
            stdout = result_func.stdout.decode('utf-8', errors='ignore')
            current_func = None

            for line in stdout.split('\n'):
                # Function 'function_name'
                func_match = re.match(r"Function '(.+)'", line)
                if func_match:
                    current_func = func_match.group(1)
                    function_coverage[current_func] = {'lines_executed': 0, 'lines_total': 0}

                # Lines executed:75.00% of 20
                elif current_func and 'Lines executed:' in line:
                    lines_match = re.search(r'Lines executed:[\d.]+% of (\d+)', line)
                    if lines_match:
                        total_lines = int(lines_match.group(1))
                        function_coverage[current_func]['lines_total'] = total_lines

                        # compute the number of executed lines
                        percent_match = re.search(r'Lines executed:([\d.]+)%', line)
                        if percent_match:
                            percent = float(percent_match.group(1))
                            executed = int(total_lines * percent / 100)
                            function_coverage[current_func]['lines_executed'] = executed

        # clean up .gcov files
        for f in source_dir.glob("*.gcov"):
            f.unlink()

        return (line_coverage, function_coverage)


class CoverageAnalyzer:
    def __init__(self, output_dir: Optional[str] = None, workspace_dir: str = "."):
        self.workspace_dir = Path(workspace_dir).resolve()

        # configure output directory
        if output_dir:
            self.output_base = Path(output_dir).resolve()
        else:
            self.output_base = self.workspace_dir / "coverage"

        self.output_base.mkdir(parents=True, exist_ok=True)

        self.html_dir = self.output_base / "html"
        self.untested_file = self.output_base / "untested.md"

    def collect_coverage_for_files(self, files: List[Path], changed_lines: Dict[str, Set[int]]) -> Dict:
        """Run gcov for each file and collect coverage"""
        print("📊 Collecting coverage data with gcov...")

        coverage_data = {}
        all_lines_mode = len(changed_lines) == 0  # --all mode

        for i, source_file in enumerate(files, 1):
            file_path = str(source_file)
            print(f"  [{i}/{len(files)}] Processing {source_file.name}...", end='\r')

            # run gcov
            result = GcovParser.run_gcov_for_file(source_file, self.workspace_dir)
            if not result:
                print(f"  ⚠️  gcov failed for {source_file.name}", file=sys.stderr)
                continue

            line_coverage, function_coverage = result

            # changed line numbers (extracted from git diff)
            changed_line_set = changed_lines.get(file_path, set()) if not all_lines_mode else set(line_coverage.keys())

            # filter to changed lines only (or full mode)
            filtered_lines = {}
            for line_num, count in line_coverage.items():
                if all_lines_mode or line_num in changed_line_set:
                    filtered_lines[line_num] = count

            # process if there are changed lines or gcov data
            if changed_line_set or filtered_lines:
                # parse function ranges and keep only functions containing changed lines
                function_ranges = SourceParser.find_function_ranges(source_file)
                filtered_functions = {}

                for func_name, func_data in function_coverage.items():
                    # look up function range
                    if func_name in function_ranges:
                        start_line, end_line = function_ranges[func_name]
                        # check if any changed line overlaps the function range
                        if any(start_line <= line <= end_line for line in changed_line_set):
                            filtered_functions[func_name] = func_data
                    else:
                        # if the function range wasn't found, include it (conservative)
                        if all_lines_mode:
                            filtered_functions[func_name] = func_data

                coverage_data[file_path] = {
                    'lines': filtered_lines,  # gcov data (executable lines only)
                    'changed_lines': changed_line_set,  # git diff data (all modified lines)
                    'total_lines': len(filtered_lines),
                    'covered_lines': sum(1 for c in filtered_lines.values() if c > 0),
                    'functions': filtered_functions,  # filtered function info
                    'function_ranges': function_ranges  # function range info
                }
            else:
                # debug: why it was skipped
                print(f"  ⚠️  Skipped {source_file.name}: changed_lines={len(changed_line_set)}, filtered_lines={len(filtered_lines)}", file=sys.stderr)

        print(f"\n✅ Coverage data collected for {len(coverage_data)} files")
        return coverage_data

    def generate_html_report(self, coverage_data: Dict) -> bool:
        """Generate HTML report (index page + per-file detail pages)"""
        print(f"📝 Generating HTML report to {self.html_dir}...")

        self.html_dir.mkdir(parents=True, exist_ok=True)

        # 1. generate main index page
        self._generate_index_page(coverage_data)

        # 2. generate detail page for each file
        for file_path, data in coverage_data.items():
            self._generate_file_page(file_path, data)

        index_file = self.output_base / "index.html"
        print(f"✅ HTML report generated: {index_file}")
        return True

    def _get_blame_info(self, source_file: Path) -> Dict[int, Tuple[str, str]]:
        """
        Collect per-line commit info via git blame
        Returns: {line_number: (short_hash, commit_message)}
        """
        blame_info = {}

        try:
            result = subprocess.run(
                ["git", "blame", "-s", "--", str(source_file)],
                cwd=self.workspace_dir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10
            )

            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore')
                for line in output.split('\n'):
                    if not line:
                        continue
                    # format: hash [filepath] line_num) code
                    # the filename is only shown when there is file-move history
                    # ex1 (with filename): c2e9b2f28818 contrib/pg_upgrade/file.c   1) /*
                    # ex2 (without filename): ^d31084e9d11   1) /*
                    match = re.match(r'^[\^]?([0-9a-f]{7,})\s+(?:\S+\s+)?(\d+)\)', line)
                    if match:
                        commit_hash = match.group(1)[:7]
                        line_num = int(match.group(2))

                        # fetch commit message (using cache)
                        if commit_hash not in getattr(self, '_commit_cache', {}):
                            if not hasattr(self, '_commit_cache'):
                                self._commit_cache = {}

                            msg_result = subprocess.run(
                                ["git", "log", "-1", "--format=%s", commit_hash],
                                cwd=self.workspace_dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=5
                            )

                            if msg_result.returncode == 0:
                                msg = msg_result.stdout.decode('utf-8', errors='ignore').strip()
                                self._commit_cache[commit_hash] = msg
                            else:
                                self._commit_cache[commit_hash] = ""

                        blame_info[line_num] = (commit_hash, self._commit_cache.get(commit_hash, ""))

        except Exception as e:
            # return an empty dict on blame failure
            pass

        return blame_info

    def _generate_index_page(self, coverage_data: Dict):
        """Generate the main index page"""
        index_file = self.output_base / "index.html"

        # compute statistics
        total_files = len(coverage_data)
        total_lines = sum(d['total_lines'] for d in coverage_data.values())
        covered_lines = sum(d['covered_lines'] for d in coverage_data.values())
        coverage_percent = (covered_lines / total_lines * 100) if total_lines > 0 else 0

        # function statistics
        total_functions = 0
        covered_functions = 0
        for data in coverage_data.values():
            if 'functions' in data:
                for func_data in data['functions'].values():
                    total_functions += 1
                    if func_data['lines_executed'] > 0:
                        covered_functions += 1

        func_coverage_pct = (covered_functions/total_functions*100 if total_functions > 0 else 0)

        with open(index_file, 'w') as f:
            f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Coverage Report - Overview</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 0; background: #f5f7fa; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color: white; padding: 30px; position: sticky; top: 0; z-index: 100;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        h1 {{ margin: 0; font-size: 2em; }}
        .subtitle {{ opacity: 0.9; margin-top: 10px; }}
        .content-wrapper {{ padding: 20px; }}
        .summary-cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
                        gap: 20px; margin-bottom: 30px; position: sticky; top: 110px; z-index: 50;
                        background: #f5f7fa; padding-top: 20px; margin-top: -20px; }}
        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card-title {{ color: #666; font-size: 0.9em; margin-bottom: 10px; }}
        .card-value {{ font-size: 2em; font-weight: bold; color: #333; }}
        .card-detail {{ color: #999; font-size: 0.9em; margin-top: 5px; }}
        .file-table {{ background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f8f9fa; padding: 15px; text-align: left; font-weight: 600; color: #495057;
             border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 12px 15px; border-bottom: 1px solid #f1f3f5; }}
        tr:hover {{ background: #f8f9fa; }}
        .file-name {{ font-family: 'Courier New', monospace; color: #495057; }}
        .file-link {{ text-decoration: none; color: #667eea; font-weight: 500; }}
        .file-link:hover {{ text-decoration: underline; }}
        .coverage-bar {{ width: 200px; height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; }}
        .coverage-fill {{ height: 100%; transition: width 0.3s; }}
        .coverage-high {{ background: linear-gradient(90deg, #51cf66, #37b24d); }}
        .coverage-medium {{ background: linear-gradient(90deg, #ffd43b, #fab005); }}
        .coverage-low {{ background: linear-gradient(90deg, #ff8787, #fa5252); }}
        .coverage-text {{ font-weight: 600; margin-left: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Coverage Report</h1>
        <div class="subtitle">Modified Lines Only</div>
    </div>

    <div class="content-wrapper">
    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Files</div>
            <div class="card-value">{total_files}</div>
            <div class="card-detail">analyzed</div>
        </div>
        <div class="card">
            <div class="card-title">Functions</div>
            <div class="card-value">{covered_functions}/{total_functions}</div>
            <div class="card-detail">{func_coverage_pct:.1f}% covered</div>
        </div>
        <div class="card">
            <div class="card-title">Lines</div>
            <div class="card-value">{covered_lines}/{total_lines}</div>
            <div class="card-detail">{coverage_percent:.1f}% covered</div>
        </div>
        <div class="card nav-help-card">
            <div class="card-title">Keyboard navigation</div>
            <div style="font-size: 0.85em; line-height: 1.8;">
                <kbd>↑</kbd> <kbd>W</kbd> previous file<br>
                <kbd>↓</kbd> <kbd>S</kbd> next file<br>
                <kbd>Enter</kbd> <kbd>Tab</kbd> open file
            </div>
        </div>
    </div>

    <div class="file-table">
        <table>
            <thead>
                <tr>
                    <th>File</th>
                    <th>Coverage</th>
                    <th style="text-align: right;">Lines</th>
                </tr>
            </thead>
            <tbody>
""")

            # file list (sorted by lowest coverage first)
            # sort: 1) by lowest coverage, 2) if coverage equal, by most modified lines
            sorted_files = sorted(coverage_data.items(),
                                key=lambda x: (
                                    x[1]['covered_lines'] / x[1]['total_lines'] if x[1]['total_lines'] > 0 else 0,
                                    -x[1]['total_lines']  # negate to sort in reverse
                                ))

            for file_path, data in sorted_files:
                try:
                    rel_path = Path(file_path).relative_to(self.workspace_dir)
                except ValueError:
                    rel_path = Path(file_path)

                file_coverage = (data['covered_lines'] / data['total_lines'] * 100) if data['total_lines'] > 0 else 0

                # coverage grade
                if file_coverage >= 80:
                    coverage_class = "coverage-high"
                elif file_coverage >= 50:
                    coverage_class = "coverage-medium"
                else:
                    coverage_class = "coverage-low"

                # preserve directory structure: src/backend/storage/smgr/md.c → html/src/backend/storage/smgr/md.c.html
                html_filename = str(rel_path) + ".html"

                f.write(f'                <tr>\n')
                f.write(f'                    <td class="file-name"><a class="file-link" href="html/{html_filename}">{rel_path}</a></td>\n')
                f.write(f'                    <td>\n')
                f.write(f'                        <div style="display: flex; align-items: center;">\n')
                f.write(f'                            <div class="coverage-bar">\n')
                f.write(f'                                <div class="coverage-fill {coverage_class}" style="width: {file_coverage}%"></div>\n')
                f.write(f'                            </div>\n')
                f.write(f'                            <span class="coverage-text">{file_coverage:.1f}%</span>\n')
                f.write(f'                        </div>\n')
                f.write(f'                    </td>\n')
                f.write(f'                    <td style="text-align: right;">{data["covered_lines"]}/{data["total_lines"]}</td>\n')
                f.write(f'                </tr>\n')

            f.write("""            </tbody>
        </table>
    </div>
    </div>

    <style>
        tr.selected-file {
            background: #e7f5ff !important;
            outline: 2px solid #667eea;
        }
        .nav-help-card kbd {
            background: #f1f3f5;
            border: 1px solid #dee2e6;
            border-radius: 3px;
            padding: 2px 6px;
            font-family: monospace;
            font-size: 0.9em;
            margin: 0 2px;
        }
    </style>

    <script>
    (function() {
        const fileRows = Array.from(document.querySelectorAll('.file-table tbody tr'));
        let currentIndex = -1;

        // check the selected file from the URL hash (#file=src/bin/pg_waldump/pg_waldump.c)
        const urlHash = window.location.hash;
        if (urlHash.startsWith('#file=')) {
            const fileName = decodeURIComponent(urlHash.substring(6));
            // locate that file
            currentIndex = fileRows.findIndex(row => {
                const link = row.querySelector('.file-link');
                return link && link.textContent === fileName;
            });
            if (currentIndex >= 0) {
                selectRow(currentIndex);
            }
        }

        function selectRow(index) {
            if (index < 0 || index >= fileRows.length) return;

            // remove previous selection
            document.querySelectorAll('.selected-file').forEach(el => el.classList.remove('selected-file'));

            // new selection
            fileRows[index].classList.add('selected-file');
            fileRows[index].scrollIntoView({ behavior: 'smooth', block: 'center' });
            currentIndex = index;
        }

        function navigateNext() {
            if (fileRows.length === 0) return;
            const nextIndex = (currentIndex + 1) % fileRows.length;
            selectRow(nextIndex);
        }

        function navigatePrev() {
            if (fileRows.length === 0) return;
            const prevIndex = currentIndex <= 0 ? fileRows.length - 1 : currentIndex - 1;
            selectRow(prevIndex);
        }

        function openSelected() {
            if (currentIndex >= 0 && currentIndex < fileRows.length) {
                const link = fileRows[currentIndex].querySelector('.file-link');
                if (link) {
                    // store the selected filename in the URL hash
                    const fileName = link.textContent;
                    window.location.href = link.href + '#back=' + encodeURIComponent(fileName);
                }
            }
        }

        // keyboard events
        document.addEventListener('keydown', function(e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            // use e.code for Korean-keyboard support (physical key position)
            switch(e.code) {
                case 'ArrowDown':
                case 'ArrowRight':
                case 'KeyS':
                case 'KeyD':
                    e.preventDefault();
                    navigateNext();
                    break;

                case 'ArrowUp':
                case 'ArrowLeft':
                case 'KeyW':
                case 'KeyA':
                    e.preventDefault();
                    navigatePrev();
                    break;

                case 'Enter':
                case 'Tab':
                    e.preventDefault();
                    openSelected();
                    break;
            }
        });

        // auto-select the first file (when no hash is present)
        if (currentIndex < 0 && fileRows.length > 0) {
            selectRow(0);
        }
    })();
    </script>
</body>
</html>
""")

    def _generate_file_page(self, file_path: str, data: Dict):
        """Generate a per-file detail page"""
        try:
            rel_path = Path(file_path).relative_to(self.workspace_dir)
        except ValueError:
            rel_path = Path(file_path)

        # preserve directory structure: src/backend/storage/smgr/md.c → html/src/backend/storage/smgr/md.c.html
        html_filename = self.html_dir / (str(rel_path) + ".html")
        html_filename.parent.mkdir(parents=True, exist_ok=True)

        # compute relative path back to index.html
        # html/src/backend/storage/smgr/md.c.html → ../../../index.html
        depth = len(rel_path.parts)  # src, backend, storage, smgr, md.c = 5 parts
        back_to_index = "../" * depth + "index.html"

        file_coverage = (data['covered_lines'] / data['total_lines'] * 100) if data['total_lines'] > 0 else 0

        # parse function ranges
        source_file = Path(file_path)
        function_ranges = data.get('function_ranges', SourceParser.find_function_ranges(source_file))

        # collect git blame info
        blame_info = self._get_blame_info(source_file)

        with open(html_filename, 'w') as f:
            f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{rel_path} - Coverage Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 0; background: #f5f7fa; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color: white; padding: 30px; position: sticky; top: 0; z-index: 100;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .back-link {{ color: white; text-decoration: none; font-size: 0.9em; opacity: 0.9; }}
        .back-link:hover {{ text-decoration: underline; opacity: 1; }}
        h1 {{ margin: 10px 0 0 0; color: white; font-size: 1.8em; }}
        .file-stats {{ margin-top: 10px; color: white; opacity: 0.9; }}
        .content-wrapper {{ padding: 20px; }}
        .summary-cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
                        gap: 20px; margin-bottom: 30px; position: sticky; top: 138px; z-index: 50;
                        background: #f5f7fa; padding-top: 20px; margin-top: -20px; }}
        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card-title {{ color: #666; font-size: 0.9em; margin-bottom: 10px; }}
        .card-value {{ font-size: 2em; font-weight: bold; color: #333; }}
        .card-detail {{ color: #999; font-size: 0.9em; margin-top: 5px; }}
        .nav-help-grid {{ font-size: 0.75em; line-height: 1.4; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
        .nav-help-col {{ display: flex; flex-direction: column; gap: 2px; }}
        .function-section {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .function-header {{ font-size: 1.2em; font-weight: 600; color: #495057; margin-bottom: 10px; font-family: 'Courier New', monospace; }}
        .function-stats {{ color: #666; margin-bottom: 15px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f8f9fa; padding: 10px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 8px 10px; border-bottom: 1px solid #f1f3f5; }}
        .line-num {{ width: 80px; text-align: right; color: #868e96; font-family: 'Courier New', monospace; font-size: 0.9em; }}
        .hit-count {{ width: 80px; text-align: right; font-family: 'Courier New', monospace; font-weight: 600; }}
        .source-code {{ font-family: 'Courier New', monospace; font-size: 0.9em; white-space: pre; padding-left: 10px; tab-size: 4; -moz-tab-size: 4; }}
        .blame-info {{ width: 600px; font-size: 0.85em; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .blame-hash {{ font-family: 'Courier New', monospace; color: #667eea; font-weight: 600; }}
        .blame-msg {{ color: #868e96; margin-left: 8px; }}
        .modified-covered {{ background: #d3f9d8; }}
        .modified-uncovered {{ background: #ffe3e3; }}
        .modified-noexec {{ background: #e7f5ff; }}
        .unmodified {{ background: #f8f9fa; color: #868e96; }}
        .hit-count.hit {{ color: #37b24d; }}
        .hit-count.miss {{ color: #f03e3e; }}
        .hit-count.na {{ color: #adb5bd; }}
        .current-line.modified-covered {{ background: #51cf66; }}
        .current-line.modified-uncovered {{ background: #ff6b6b; }}
        kbd {{ background: #f1f3f5; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 0.9em; border: 1px solid #dee2e6; margin: 0 2px; }}
    </style>
</head>
<body>
    <div class="header">
        <a href="{back_to_index}" class="back-link">← Back to Overview</a>
        <h1>{rel_path}</h1>
        <div class="file-stats">
            <strong>Coverage:</strong> {data['covered_lines']}/{data['total_lines']} lines ({file_coverage:.1f}%)
        </div>
    </div>

    <div class="content-wrapper">

    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Total Lines</div>
            <div class="card-value">{data['total_lines']}</div>
            <div class="card-detail">modified</div>
        </div>
        <div class="card">
            <div class="card-title">Covered</div>
            <div class="card-value">{data['covered_lines']}</div>
            <div class="card-detail">{file_coverage:.1f}%</div>
        </div>
        <div class="card">
            <div class="card-title">Uncovered</div>
            <div class="card-value">{data['total_lines'] - data['covered_lines']}</div>
            <div class="card-detail">{100 - file_coverage:.1f}%</div>
        </div>
        <div class="card">
            <div class="card-title">Keyboard navigation</div>
            <div class="nav-help-grid">
                <div class="nav-help-col">
                    <div><kbd>↑</kbd><kbd>W</kbd> prev uncovered</div>
                    <div><kbd>↓</kbd><kbd>S</kbd> next uncovered</div>
                    <div><kbd>←</kbd><kbd>A</kbd> prev modified</div>
                    <div><kbd>→</kbd><kbd>D</kbd> next modified</div>
                </div>
                <div class="nav-help-col">
                    <div><kbd>PgUp</kbd><kbd>Q</kbd> prev function</div>
                    <div><kbd>PgDn</kbd><kbd>E</kbd> next function</div>
                    <div><kbd>Home</kbd><kbd>C-A</kbd> top</div>
                    <div><kbd>End</kbd><kbd>C-E</kbd> bottom</div>
                </div>
                <div class="nav-help-col">
                    <div><kbd>ESC</kbd><kbd>BS</kbd> index</div>
                </div>
            </div>
        </div>
    </div>
""")

            # read source file
            try:
                with open(source_file, 'r', encoding='utf-8', errors='ignore') as src:
                    source_lines = src.readlines()
            except Exception as e:
                source_lines = []

            # all modified lines extracted via git diff (including comments and non-executable lines)
            all_changed_lines = data.get('changed_lines', set())

            # group modified lines by function
            functions_with_changes = {}
            unassigned_lines = []

            for line_num in sorted(all_changed_lines):
                found = False
                for func_name, (start, end) in function_ranges.items():
                    if start <= line_num <= end:
                        if func_name not in functions_with_changes:
                            functions_with_changes[func_name] = (start, end)
                        found = True
                        break
                if not found:
                    unassigned_lines.append(line_num)

            # per-function sections (only functions with modified lines, sorted by line number)
            for func_name in sorted(functions_with_changes.keys(), key=lambda f: functions_with_changes[f][0]):
                start_line, end_line = functions_with_changes[func_name]

                # compute coverage from modified lines in the function (only those with gcov data)
                modified_lines_in_func = [ln for ln in all_changed_lines if start_line <= ln <= end_line and ln in data['lines']]
                func_total = len(modified_lines_in_func)
                func_covered = sum(1 for ln in modified_lines_in_func if data['lines'][ln] > 0)
                func_coverage = (func_covered / func_total * 100) if func_total > 0 else 0

                f.write(f'    <div class="function-section">\n')
                f.write(f'        <div class="function-header">{func_name}() <span style="color: #adb5bd; font-size: 0.8em;">lines {start_line}-{end_line}</span></div>\n')
                f.write(f'        <div class="function-stats">Modified Lines Coverage: {func_covered}/{func_total} lines ({func_coverage:.1f}%)</div>\n')
                f.write(f'        <table>\n')
                f.write(f'            <tr><th>Line</th><th>Hits</th><th>Source</th><th>Commit</th></tr>\n')

                # show every line within the function range
                for line_num in range(start_line, end_line + 1):
                    # fetch source code
                    if source_lines and 0 < line_num <= len(source_lines):
                        source_code = source_lines[line_num - 1].rstrip('\n')
                    else:
                        source_code = ""

                    # check if the line is modified (per git diff)
                    is_modified = line_num in all_changed_lines

                    if is_modified:
                        # check if gcov data exists
                        hit_count = data['lines'].get(line_num, None)
                        if hit_count is not None:
                            # executable line
                            row_class = "modified-covered" if hit_count > 0 else "modified-uncovered"
                            hit_class = "hit" if hit_count > 0 else "miss"
                            hit_display = str(hit_count)
                        else:
                            # non-executable line (comment, declaration, etc.)
                            row_class = "modified-noexec"
                            hit_class = "na"
                            hit_display = "-"
                    else:
                        # unmodified line
                        row_class = "unmodified"
                        hit_class = "na"
                        hit_display = "-"

                    # HTML escape
                    source_code = source_code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

                    # blame info (shown only for modified lines)
                    if is_modified and line_num in blame_info:
                        commit_hash, commit_msg = blame_info[line_num]
                        commit_msg_escaped = commit_msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        blame_html = f'<span class="blame-hash">{commit_hash}</span><span class="blame-msg">{commit_msg_escaped}</span>'
                    else:
                        blame_html = '<span class="blame-msg">-</span>'

                    f.write(f'            <tr class="{row_class}">\n')
                    f.write(f'                <td class="line-num">{line_num}</td>\n')
                    f.write(f'                <td class="hit-count {hit_class}">{hit_display}</td>\n')
                    f.write(f'                <td class="source-code">{source_code}</td>\n')
                    f.write(f'                <td class="blame-info">{blame_html}</td>\n')
                    f.write(f'            </tr>\n')

                f.write(f'        </table>\n')
                f.write(f'    </div>\n')

            f.write("""    </div>

    <script>
    (function() {
        // find modified lines (exclude blue - only green and red)
        const modifiedRows = Array.from(document.querySelectorAll('tr.modified-covered, tr.modified-uncovered'));
        // find only uncovered lines (red)
        const untestedRows = Array.from(document.querySelectorAll('tr.modified-uncovered'));
        // find all function sections
        const functionSections = Array.from(document.querySelectorAll('.function-section'));

        // group consecutive lines together (only same-color runs)
        function groupConsecutiveRows(rows) {
            if (rows.length === 0) return [];

            const groups = [];
            let currentGroup = [rows[0]];

            for (let i = 1; i < rows.length; i++) {
                const prevRow = currentGroup[currentGroup.length - 1];
                const currRow = rows[i];

                const prevLineNum = parseInt(prevRow.querySelector('.line-num').textContent);
                const currLineNum = parseInt(currRow.querySelector('.line-num').textContent);

                // check if they are the same color (green: modified-covered, red: modified-uncovered)
                const prevIsCovered = prevRow.classList.contains('modified-covered');
                const currIsCovered = currRow.classList.contains('modified-covered');
                const sameColor = prevIsCovered === currIsCovered;

                // consecutive line numbers with the same color belong to the same group
                if (currLineNum === prevLineNum + 1 && sameColor) {
                    currentGroup.push(currRow);
                } else {
                    // start a new group
                    groups.push(currentGroup);
                    currentGroup = [currRow];
                }
            }
            groups.push(currentGroup);
            return groups;
        }

        const modifiedGroups = groupConsecutiveRows(modifiedRows);
        const untestedGroups = groupConsecutiveRows(untestedRows);

        // track index of the currently selected group
        let currentModifiedIndex = -1;
        let currentUntestedIndex = -1;

        // find currently selected group (row with the current-line class)
        function findCurrentIndex(groups) {
            for (let i = 0; i < groups.length; i++) {
                for (let row of groups[i]) {
                    if (row.classList.contains('current-line')) {
                        return i;
                    }
                }
            }
            return -1;
        }

        function findClosestFunctionIndex() {
            if (functionSections.length === 0) return -1;

            const viewportCenter = window.scrollY + window.innerHeight / 2;
            let closestIndex = 0;
            let minDistance = Math.abs(functionSections[0].getBoundingClientRect().top + window.scrollY - viewportCenter);

            for (let i = 1; i < functionSections.length; i++) {
                const distance = Math.abs(functionSections[i].getBoundingClientRect().top + window.scrollY - viewportCenter);
                if (distance < minDistance) {
                    minDistance = distance;
                    closestIndex = i;
                }
            }

            return closestIndex;
        }

        function scrollToGroup(group) {
            if (!group || group.length === 0) return;

            // remove previous highlight
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));

            // add highlight to every line in the group
            group.forEach(row => row.classList.add('current-line'));

            // scroll to the first line of the group
            group[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        let lastFunctionScrolled = null;

        function scrollToFunction(section) {
            if (!section) return;

            // remove highlight
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));

            // compute sticky header height
            const header = document.querySelector('.header');
            const summaryCards = document.querySelector('.summary-cards');
            const headerHeight = header ? header.offsetHeight : 0;
            const cardsHeight = summaryCards ? summaryCards.offsetHeight : 0;
            const totalStickyHeight = headerHeight + cardsHeight + 20; // 20px padding

            // compute function section position and scroll
            const sectionTop = section.getBoundingClientRect().top + window.scrollY;
            window.scrollTo({
                top: sectionTop - totalStickyHeight,
                behavior: 'smooth'
            });

            // record the last scrolled-to function
            lastFunctionScrolled = section;
        }

        // find groups of modified lines within a function
        function findGroupsInFunction(section, groups) {
            if (!section) return [];

            const sectionRows = Array.from(section.querySelectorAll('tr.modified-covered, tr.modified-uncovered'));
            return groups.filter(group => sectionRows.includes(group[0]));
        }

        function navigateNext() {
            if (modifiedGroups.length === 0) return;

            // if we just moved to a function, jump to its first modified line
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, modifiedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentModifiedIndex = modifiedGroups.indexOf(groupsInFunction[0]);
                    scrollToGroup(groupsInFunction[0]);
                    return;
                }
            }

            // update current index
            currentModifiedIndex = findCurrentIndex(modifiedGroups);
            if (currentModifiedIndex === -1) {
                currentModifiedIndex = 0;
            } else {
                currentModifiedIndex = (currentModifiedIndex + 1) % modifiedGroups.length;
            }
            scrollToGroup(modifiedGroups[currentModifiedIndex]);
        }

        function navigatePrev() {
            if (modifiedGroups.length === 0) return;

            // if we just moved to a function, jump to its last modified line
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, modifiedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentModifiedIndex = modifiedGroups.indexOf(groupsInFunction[groupsInFunction.length - 1]);
                    scrollToGroup(groupsInFunction[groupsInFunction.length - 1]);
                    return;
                }
            }

            // update current index
            currentModifiedIndex = findCurrentIndex(modifiedGroups);
            if (currentModifiedIndex === -1) {
                currentModifiedIndex = modifiedGroups.length - 1;
            } else {
                currentModifiedIndex = currentModifiedIndex <= 0 ? modifiedGroups.length - 1 : currentModifiedIndex - 1;
            }
            scrollToGroup(modifiedGroups[currentModifiedIndex]);
        }

        function navigateUntestedNext() {
            if (untestedGroups.length === 0) return;

            // if we just moved to a function, jump to its first uncovered line
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, untestedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentUntestedIndex = untestedGroups.indexOf(groupsInFunction[0]);
                    scrollToGroup(groupsInFunction[0]);
                    return;
                }
            }

            // update current index
            currentUntestedIndex = findCurrentIndex(untestedGroups);
            if (currentUntestedIndex === -1) {
                currentUntestedIndex = 0;
            } else {
                currentUntestedIndex = (currentUntestedIndex + 1) % untestedGroups.length;
            }
            scrollToGroup(untestedGroups[currentUntestedIndex]);
        }

        function navigateUntestedPrev() {
            if (untestedGroups.length === 0) return;

            // if we just moved to a function, jump to its last uncovered line
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, untestedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentUntestedIndex = untestedGroups.indexOf(groupsInFunction[groupsInFunction.length - 1]);
                    scrollToGroup(groupsInFunction[groupsInFunction.length - 1]);
                    return;
                }
            }

            // update current index
            currentUntestedIndex = findCurrentIndex(untestedGroups);
            if (currentUntestedIndex === -1) {
                currentUntestedIndex = untestedGroups.length - 1;
            } else {
                currentUntestedIndex = currentUntestedIndex <= 0 ? untestedGroups.length - 1 : currentUntestedIndex - 1;
            }
            scrollToGroup(untestedGroups[currentUntestedIndex]);
        }

        function navigateFunctionNext() {
            if (functionSections.length === 0) return;

            const currentIndex = findClosestFunctionIndex();
            const nextIndex = currentIndex + 1;
            // prevent rollover: don't advance past the last function
            if (nextIndex >= functionSections.length) return;
            scrollToFunction(functionSections[nextIndex]);
        }

        function navigateFunctionPrev() {
            if (functionSections.length === 0) return;

            const currentIndex = findClosestFunctionIndex();
            const prevIndex = currentIndex - 1;
            // prevent rollover: don't go before the first function
            if (prevIndex < 0) return;
            scrollToFunction(functionSections[prevIndex]);
        }

        function jumpToTop() {
            // remove highlight
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));
            // scroll to top of page
            window.scrollTo({ top: 0, behavior: 'smooth' });
            lastFunctionScrolled = null;
        }

        function jumpToBottom() {
            // remove highlight
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));
            // scroll to bottom of page
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
            lastFunctionScrolled = null;
        }

        function goToIndex() {
            // pass the current filename via hash so index.html selects it
            const fileName = """ + f'"{rel_path}"' + """;
            window.location.href = """ + f'"{back_to_index}"' + """ + "#file=" + encodeURIComponent(fileName);
        }

        // keyboard events
        document.addEventListener('keydown', function(e) {
            // ignore in input fields
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            // Ctrl-A (prevent default select-all) - use e.key (Ctrl combos are language-agnostic)
            if (e.ctrlKey && e.key === 'a') {
                e.preventDefault();
                jumpToTop();
                return;
            }

            // Ctrl-E - use e.key (Ctrl combos are language-agnostic)
            if (e.ctrlKey && e.key === 'e') {
                e.preventDefault();
                jumpToBottom();
                return;
            }

            // use e.code for Korean-keyboard support (physical key position)
            switch(e.code) {
                case 'Home':
                    e.preventDefault();
                    jumpToTop();
                    break;

                case 'End':
                    e.preventDefault();
                    jumpToBottom();
                    break;

                case 'Escape':
                case 'Backspace':
                    e.preventDefault();
                    goToIndex();
                    break;

                case 'ArrowLeft':
                case 'KeyA':
                    e.preventDefault();
                    navigatePrev();
                    break;

                case 'ArrowRight':
                case 'KeyD':
                    e.preventDefault();
                    navigateNext();
                    break;

                case 'ArrowUp':
                case 'KeyW':
                    e.preventDefault();
                    navigateUntestedPrev();
                    break;

                case 'ArrowDown':
                case 'KeyS':
                    e.preventDefault();
                    navigateUntestedNext();
                    break;

                case 'KeyQ':
                case 'PageUp':
                    e.preventDefault();
                    navigateFunctionPrev();
                    break;

                case 'KeyE':
                case 'PageDown':
                    e.preventDefault();
                    navigateFunctionNext();
                    break;
            }
        });
    })();
    </script>
</body>
</html>
""")

    def generate_untested_report(self, coverage_data: Dict) -> bool:
        """Generate a Markdown checklist of untested line information"""
        print(f"📋 Generating untested lines report to {self.untested_file}...")

        with open(self.untested_file, 'w') as f:
            f.write("# Untested Code Analysis Report (modified lines only)\n\n")
            f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")

            total_files = len(coverage_data)
            untested_files = 0
            total_lines = 0
            untested_lines = 0

            # per-file details
            for file_path, data in sorted(coverage_data.items()):
                try:
                    rel_path = Path(file_path).relative_to(self.workspace_dir)
                except ValueError:
                    rel_path = file_path

                file_total_lines = data['total_lines']
                file_covered_lines = data['covered_lines']
                file_untested_lines = file_total_lines - file_covered_lines

                # aggregate overall statistics (include all files)
                total_lines += file_total_lines
                untested_lines += file_untested_lines

                # skip detailed output for fully covered files
                if file_untested_lines == 0:
                    continue

                untested_files += 1

                coverage_percent = (file_covered_lines / file_total_lines * 100) if file_total_lines > 0 else 0

                f.write(f"## 📄 {rel_path}\n\n")
                f.write(f"**File coverage**: {file_covered_lines}/{file_total_lines} lines ({coverage_percent:.1f}%)\n\n")

                # parse function ranges
                source_file = Path(file_path)
                function_ranges = SourceParser.find_function_ranges(source_file)

                # untested lines
                untested_line_nums = sorted([ln for ln, count in data['lines'].items() if count == 0])

                if not untested_line_nums:
                    continue

                # group untested lines by function
                lines_by_function = defaultdict(list)
                unassigned_lines = []

                for line_num in untested_line_nums:
                    # find which function it belongs to
                    found = False
                    for func_name, (start, end) in function_ranges.items():
                        if start <= line_num <= end:
                            lines_by_function[func_name].append(line_num)
                            found = True
                            break
                    if not found:
                        unassigned_lines.append(line_num)

                # output per function
                for func_name in sorted(lines_by_function.keys()):
                    func_lines = lines_by_function[func_name]
                    start_line, end_line = function_ranges[func_name]

                    # total line count inside the function (modified lines only)
                    func_total_lines = sum(1 for ln in data['lines'].keys() if start_line <= ln <= end_line)
                    func_covered_lines = sum(1 for ln, count in data['lines'].items()
                                           if start_line <= ln <= end_line and count > 0)
                    func_coverage = (func_covered_lines / func_total_lines * 100) if func_total_lines > 0 else 0

                    f.write(f"### Function: `{func_name}` (lines {start_line}-{end_line})\n\n")
                    f.write(f"**Function coverage**: {func_covered_lines}/{func_total_lines} lines ({func_coverage:.1f}%)\n\n")

                    # group into consecutive ranges
                    ranges = []
                    range_start = func_lines[0]
                    range_end = func_lines[0]

                    for line_num in func_lines[1:]:
                        if line_num == range_end + 1:
                            range_end = line_num
                        else:
                            if range_start == range_end:
                                ranges.append(f"{range_start}")
                            else:
                                ranges.append(f"{range_start}-{range_end}")
                            range_start = line_num
                            range_end = line_num

                    # final range
                    if range_start == range_end:
                        ranges.append(f"{range_start}")
                    else:
                        ranges.append(f"{range_start}-{range_end}")

                    f.write(f"#### 🔴 Untested lines ({len(ranges)} ranges)\n\n")
                    for line_range in ranges:
                        f.write(f"- [ ] `{rel_path}:{line_range}`\n")
                    f.write("\n")

                f.write("---\n\n")

            # compute function statistics
            total_functions = 0
            covered_functions = 0

            for file_path, data in coverage_data.items():
                if 'functions' in data:
                    for func_name, func_data in data['functions'].items():
                        total_functions += 1
                        if func_data['lines_executed'] > 0:
                            covered_functions += 1

            # summary statistics
            f.write("## 📊 Summary Statistics\n\n")
            f.write(f"- **Files**: {untested_files}/{total_files} files contain untested code\n")

            if total_functions > 0:
                uncovered_functions = total_functions - covered_functions
                func_coverage = covered_functions / total_functions * 100
                f.write(f"- **Functions**: {covered_functions}/{total_functions} functions covered "
                       f"({func_coverage:.1f}%)\n")
                f.write(f"  - Untested: {uncovered_functions} functions\n")

            if total_lines > 0:
                covered_lines = total_lines - untested_lines
                line_coverage = covered_lines / total_lines * 100
                f.write(f"- **Lines**: {covered_lines}/{total_lines} lines covered "
                       f"({line_coverage:.1f}%) - **modified lines only**\n")
                f.write(f"  - Untested: {untested_lines} lines\n")

            f.write("\n---\n\n")
            f.write("## 💡 Usage Guide\n\n")
            f.write("1. Change `- [ ]` at the front of each item to `- [x]` to mark work as done\n")
            f.write("2. The `path:line` or `path:start-end` format lets you identify the exact location\n")
            f.write("3. Provide this file to Claude Code and request test cases\n")
            f.write("   - e.g. \"Write whitebox tests for the lines in untested.md\"\n")
            f.write("4. **Note**: This report only includes modified lines (not full-file coverage)\n")

        print(f"✅ Untested lines report generated: {self.untested_file}")
        return True

    def run(self, commit_range: str) -> bool:
        """Run the full pipeline"""
        print(f"🚀 Starting coverage analysis for: {commit_range or 'all files'}")
        print(f"📁 Output directory: {self.output_base}\n")

        # 1. extract changed lines via git diff
        print("🔍 Analyzing git diff for changed lines...")
        changed_lines = GitDiffParser.get_changed_lines(self.workspace_dir, commit_range)

        # 2. list of changed files
        if commit_range != "--all":
            if not changed_lines:
                print("❌ No .c or .h files changed in the commit range")
                return False

            files = [Path(f) for f in changed_lines.keys()]
            total_changed_lines = sum(len(lines) for lines in changed_lines.values())
            print(f"📁 Found {len(files)} files with {total_changed_lines} changed lines\n")
        else:
            # full mode: every .c, .h file
            print("📁 Scanning all source files...")
            files = list(self.workspace_dir.rglob("*.c")) + list(self.workspace_dir.rglob("*.h"))
            files = [f for f in files if f.exists()]
            print(f"📁 Found {len(files)} source files\n")

        # 3. collect coverage via gcov
        coverage_data = self.collect_coverage_for_files(files, changed_lines)

        if not coverage_data:
            print("❌ No coverage data collected")
            return False

        # 4. generate HTML report
        if not self.generate_html_report(coverage_data):
            return False

        # 5. generate untested lines report
        if not self.generate_untested_report(coverage_data):
            return False

        print("\n" + "=" * 80)
        print("✅ Coverage analysis complete!")
        print("=" * 80)
        print(f"📊 HTML Report  : {self.output_base / 'index.html'}")
        print(f"📋 Untested Code: {self.untested_file}")
        print(f"📁 All outputs  : {self.output_base}")
        print("=" * 80)

        return True


def main():
    parser = argparse.ArgumentParser(
        description='Generate code coverage report based on git commits (modified lines only, using gcov)',
        epilog='''
Examples:
  ./gencov.py abc123              # only lines modified in commit abc123
  ./gencov.py HEAD~5              # only lines modified in the last 5 commits
  ./gencov.py abc123..def456      # only lines modified in the commit range
  ./gencov.py -o coverage master  # specify output directory
  ./gencov.py --all               # full source (all lines)
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'commit_range',
        nargs='?',
        help='Git commit or range (e.g. HEAD~10, abc123, abc123..def456, --all)'
    )

    parser.add_argument(
        '-o', '--output-dir',
        help='Output directory (default: coverage)'
    )

    args = parser.parse_args()

    if not args.commit_range:
        parser.print_help()
        sys.exit(1)

    analyzer = CoverageAnalyzer(output_dir=args.output_dir)
    success = analyzer.run(args.commit_range)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
