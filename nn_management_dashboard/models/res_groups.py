# -*- coding: utf-8 -*-
from odoo import api, models


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.model
    def get_groups_by_application(self):
        # A category holding a single group (or a chain of groups) is drawn as a dropdown on
        # the user form. Insights is a set of independent dashboards, so draw it the way
        # Extra Rights are drawn: an "Insights" heading with one checkbox per dashboard.
        res = super().get_groups_by_application()
        insights = self.env.ref("nn_management_dashboard.module_category_insights", raise_if_not_found=False)
        if not insights:
            return res
        return [(app, "boolean", groups, (100, "Other")) if app == insights else (app, kind, groups, name)
                for app, kind, groups, name in res]
