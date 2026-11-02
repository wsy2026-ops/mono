# -*- coding: utf-8 -*-
"""adminpanel/ — 管理后台包（app.py 拆包）。

56 个路由：用户管理/内容审核/惩罚/数据看板/监控/AI 管理。
endpoint 名与函数名一致，url_for 零改动。
"""
from . import deps as _deps

D = _deps.D


def init(app, deps=None):
    if deps:
        D.update(deps)
    from . import core
    _register(app, D, core)


def _register(app, D, core):
    LR, AR, SA = D["login_required"], D["admin_required"], D["super_admin_required"]
    GUARD = {"LR": LR, "AR": AR, "SA": SA}
    R = [
        ("/admin/home-banners", "admin_home_banners", ['GET', 'POST'], "SA"),
        ("/admin/metrics", "admin_metrics", None, "AR"),
        ("/admin/points-audit", "admin_points_audit", None, "AR"),
        ("/admin/clawback/preview/<int:uid>", "admin_clawback_preview", None, "AR"),
        ("/admin/clawback", "admin_clawback", ['POST'], "AR"),
        ("/admin/perf", "admin_perf", None, "AR"),
        ("/admin/activity", "admin_activity", None, "SA"),
        ("/admin/debug", "admin_debug", None, "SA"),
        ("/admin/pages", "admin_pages", ['GET', 'POST'], "SA"),
        ("/admin/pages/reset", "admin_pages_reset", ['POST'], "SA"),
        ("/admin/changelog", "admin_changelog", ['GET', 'POST'], "SA"),
        ("/admin/changelog/<int:cid>/edit", "admin_changelog_edit", ['POST'], "SA"),
        ("/admin/changelog/<int:cid>/delete", "admin_changelog_delete", ['POST'], "SA"),
        ("/admin/force-unfriend/<int:user_id>", "admin_force_unfriend", ['POST'], "SA"),
        ("/api/admin/force-unfriend", "api_admin_force_unfriend", ['POST'], "SA"),
        ("/admin/recycle", "admin_recycle", None, "AR"),
        ("/admin/recycle/bulk", "admin_recycle_bulk", ['POST'], "AR"),
        ("/admin/messages", "admin_messages", None, "AR"),
        ("/admin/messages/bulk", "admin_messages_bulk", ['POST'], "AR"),
        ("/admin/clear-comments/<int:post_id>", "admin_clear_comments", ['POST'], "AR"),
        ("/admin/add-admin", "admin_add_admin", ['POST'], "SA"),
        ("/admin/remove-admin/<int:user_id>", "admin_remove_admin", ['POST'], "SA"),
        ("/admin", "admin", None, "AR"),
        ("/admin/ban", "admin_ban", ['POST'], "AR"),
        ("/admin/mute", "admin_mute", ['POST'], "AR"),
        ("/admin/give-effect/<int:user_id>", "admin_give_effect", ['POST'], "SA"),
        ("/admin/give-badge/<int:user_id>", "admin_give_badge", ['POST'], "SA"),
        ("/admin/batch-posts", "admin_batch_posts", ['POST'], "AR"),
        ("/admin/delete-post/<int:post_id>", "admin_delete_post", ['POST'], "AR"),
        ("/admin/appeals", "admin_appeals", None, "AR"),
        ("/admin/appeal/<int:aid>/<action>", "admin_appeal_review", ['POST'], "SA"),
        ("/admin/reports", "admin_reports", None, "AR"),
        ("/admin/report/<int:rid>/ignore", "admin_report_ignore", ['POST'], "AR"),
        ("/admin/report/<int:rid>/punish", "admin_report_punish", ['POST'], "AR"),
        ("/admin/report/<int:rid>/escalate", "admin_report_escalate", ['POST'], "AR"),
        ("/admin/sensitive", "admin_sensitive", ['GET', 'POST'], "SA"),
        ("/admin/punishments", "admin_punishments", None, "AR"),
        ("/admin/recall-logs", "admin_recall_logs", None, "AR"),
        ("/admin/admin-logs", "admin_admin_logs", None, "SA"),
        ("/admin/monitor", "admin_monitor", None, "AR"),
        ("/api/admin/monitor/live", "api_monitor_live", None, "AR"),
        ("/api/admin/monitor/series", "api_monitor_series", None, "AR"),
        ("/api/admin/monitor/sources", "api_monitor_sources", None, "AR"),
        ("/api/admin/monitor/events", "api_monitor_events", None, "AR"),
        ("/admin/api/deep", "admin_api_deep", None, "AR"),
        ("/admin/insights", "admin_insights", None, "AR"),
        ("/admin/api/insights", "admin_api_insights", None, "AR"),
        ("/admin/rooms", "admin_rooms", ['GET', 'POST'], "SA"),
        ("/api/admin/give-points", "api_admin_give_points", ['POST'], "SA"),
        ("/admin/set-ai-quota", "admin_set_ai_quota", ['POST'], "SA"),
        ("/api/admin/users/batch", "api_admin_users_batch", ['POST'], "AR"),
        ("/api/admin/ai-quota", "api_admin_set_ai_quota", ['POST'], "SA"),
        ("/admin/ai-codes", "admin_ai_codes", ['GET', 'POST'], "SA"),
        ("/admin/ai-codes/<int:cid>/del", "admin_ai_code_del", ['POST'], "SA"),
        ("/admin/server", "admin_server_status", None, "SA"),
        ("/admin/ai-logs", "admin_ai_logs", None, "AR"),
        ("/admin/ai-square-usage", "admin_aisq_usage", None, "SA"),
        ("/admin/update-notice", "admin_update_notice", ['GET', 'POST'], "SA"),
        ("/admin/api/aisq-users", "admin_api_aisq_users", ['GET'], "SA"),
        ("/admin/survey", "admin_survey", ['GET', 'POST'], "SA"),
        ("/admin/survey/<int:sid>/results", "admin_survey_results", ['GET'], "SA"),
        ("/admin/survey/<int:sid>/toggle", "admin_survey_toggle", ['POST'], "SA"),
        ("/admin/survey/<int:sid>/delete", "admin_survey_delete", ['POST'], "SA"),
        ("/admin/aisq-user/<int:uid>", "admin_aisq_user", ['GET'], "SA"),
        ("/admin/aisq-conv/<int:cid>", "admin_aisq_conv", ['GET'], "SA"),
    ]
    for rule, ep, methods, guard in R:
        fn = getattr(core, ep)
        app.add_url_rule(rule, ep, GUARD[guard](fn), methods=methods)
