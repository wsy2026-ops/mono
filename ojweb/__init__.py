# -*- coding: utf-8 -*-
"""ojweb/ — MONO OJ Web 层包（app.py 拆包）。

题库/比赛/提交/记录/后台管理；判题机在 oj/ 目录（judge_server.py :8081）。
endpoint 名与函数名一致，url_for 零改动。
"""
from . import deps as _deps

D = _deps.D


def init(app, deps=None):
    if deps:
        D.update(deps)
    from . import core
    # 模板过滤器随搬随注册（dt_local 被全站模板使用）
    app.template_filter("dt_local")(core._oj_dt_local)
    _register(app, D, core)


def _register(app, D, core):
    from .core import (
        oj_admin, oj_admin_contest_new, oj_admin_contest, oj_admin_contest_save,
        oj_admin_contest_delete, oj_admin_problem_new, oj_admin_problem,
        oj_admin_problem_save, oj_admin_problem_upload, oj_admin_problem_cases,
        oj_admin_problem_delete, oj_home, oj_contest, oj_contest_rank,
        oj_record, oj_record_json, oj_records, oj_problem, oj_submit,
        oj_test, oj_test_status,
        oj_problem_new, oj_problem_create, oj_problem_edit, oj_problem_update,
        oj_problem_delete_own, oj_admin_review, oj_admin_review_do,
        oj_contest_list, oj_problem_upload_own,
        oj_contest_problems, oj_contest_attach, oj_contest_detach)
    LR = D["login_required"]
    R = [
        ("/oj/admin", "oj_admin", None, oj_admin, LR),
        ("/oj/admin/contest/new", "oj_admin_contest_new", ["POST"], oj_admin_contest_new, LR),
        ("/oj/admin/contest/<int:cid>", "oj_admin_contest", None, oj_admin_contest, LR),
        ("/oj/admin/contest/<int:cid>/save", "oj_admin_contest_save", ["POST"], oj_admin_contest_save, LR),
        ("/oj/admin/contest/<int:cid>/delete", "oj_admin_contest_delete", ["POST"], oj_admin_contest_delete, LR),
        ("/oj/admin/contest/<int:cid>/problem/new", "oj_admin_problem_new", ["POST"], oj_admin_problem_new, LR),
        ("/oj/admin/problem/<int:pid>", "oj_admin_problem", None, oj_admin_problem, LR),
        ("/oj/admin/problem/<int:pid>/save", "oj_admin_problem_save", ["POST"], oj_admin_problem_save, LR),
        ("/oj/admin/problem/<int:pid>/upload", "oj_admin_problem_upload", ["POST"], oj_admin_problem_upload, LR),
        ("/oj/admin/problem/<int:pid>/cases", "oj_admin_problem_cases", ["POST"], oj_admin_problem_cases, LR),
        ("/oj/admin/problem/<int:pid>/delete", "oj_admin_problem_delete", ["POST"], oj_admin_problem_delete, LR),
        ("/oj", "oj_home", None, oj_home, None),
        ("/oj/contests", "oj_contest_list", None, oj_contest_list, None),
        ("/oj/contest/<int:cid>", "oj_contest", None, oj_contest, LR),
        ("/oj/contest/<int:cid>/problems", "oj_contest_problems", None, oj_contest_problems, LR),
        ("/oj/contest/<int:cid>/attach", "oj_contest_attach", ["POST"], oj_contest_attach, LR),
        ("/oj/contest/<int:cid>/detach", "oj_contest_detach", ["POST"], oj_contest_detach, LR),
        ("/oj/problem/<int:pid>/upload", "oj_problem_upload_own", ["POST"], oj_problem_upload_own, LR),
        ("/oj/contest/<int:cid>/rank", "oj_contest_rank", None, oj_contest_rank, LR),
        ("/oj/record/<int:sid>", "oj_record", None, oj_record, LR),
        ("/oj/record/<int:sid>/json", "oj_record_json", None, oj_record_json, LR),
        ("/oj/records", "oj_records", None, oj_records, LR),
        ("/oj/problem/<int:pid>", "oj_problem", None, oj_problem, None),
        ("/oj/problem/new", "oj_problem_new", None, oj_problem_new, LR),
        ("/oj/problem/create", "oj_problem_create", ["POST"], oj_problem_create, LR),
        ("/oj/problem/<int:pid>/edit", "oj_problem_edit", None, oj_problem_edit, LR),
        ("/oj/problem/<int:pid>/update", "oj_problem_update", ["POST"], oj_problem_update, LR),
        ("/oj/problem/<int:pid>/delete", "oj_problem_delete_own", ["POST"], oj_problem_delete_own, LR),
        ("/oj/admin/review", "oj_admin_review", None, oj_admin_review, LR),
        ("/oj/admin/review/<int:pid>", "oj_admin_review_do", ["POST"], oj_admin_review_do, LR),
        ("/oj/submit", "oj_submit", ["POST"], oj_submit, LR),
        ("/oj/test", "oj_test", ["POST"], oj_test, LR),
        ("/oj/status/<tid>", "oj_test_status", None, oj_test_status, LR),
        ("/oj/test/<tid>", "oj_test_status_t", None, oj_test_status, LR),
    ]
    for rule, ep, methods, fn, guard in R:
        app.add_url_rule(rule, ep, guard(fn) if guard else fn, methods=methods)
