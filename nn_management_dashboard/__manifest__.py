# -*- coding: utf-8 -*-
{
    'name': "Management Dashboard",

    'summary': "Command Rail — the eight portfolio-health measures owners ask for first",

    'description': """
Eight measures computed live from the property, contract and accounting models already
in this database: Rent Arrears, Bounced Cheques, Leases Nearing Expiry, Vacant Units,
Leases Below Target Rent, Cost Overview, Unpaid Dues and Unearned Income. One fixed
layout (Command Rail), one fixed palette (Doha Sand) and one fixed typeface (Modern).
    """,

    'author': "AGM",
    'category': 'Real Estate',
    'version': '0.1',

    'depends': ['base', 'web', 'account', 'account_budget',
                'nn_property_management', 'nn_thirdparty_contract', 'nn_budgeting_management'],

    "assets": {
        "web.assets_backend": [
            "https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700"
            "&family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap",
            "nn_management_dashboard/static/src/scss/management_dashboard.scss",
            "nn_management_dashboard/static/src/js/**/*",
            "nn_management_dashboard/static/src/xml/**/*",
        ]
    },

    'data': [
        'security/ir_security.xml',
        'security/ir.model.access.csv',
        'views/management_dashboard_action.xml',
    ],
}
