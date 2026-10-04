/** @odoo-module **/

import { Component, onMounted, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/webclient/actions/action_hook";
import { cookie } from "@web/core/browser/cookie";

const PRESETS = [
    { id: "d7", label: "7d", days: 7 },
    { id: "d30", label: "30d", days: 30 },
    { id: "d60", label: "60d", days: 60 },
    { id: "d90", label: "90d", days: 90 },
    { id: "mtd", label: "Month" },
    { id: "ytd", label: "Year" },
    { id: "all", label: "All time" },
];

const MEASURE_ORDER = ["arrears", "cheques", "leases", "vacant", "below", "cost", "dues", "unearned"];

const STATUS_LABEL = { good: "Clear", warn: "Watch", crit: "Breach" };
const PROV_LABEL = { exact: "exact", snap: "snapshot" };

function iso(d) {
    return d.toISOString().slice(0, 10);
}
function parseIso(s) {
    return new Date(s + "T00:00:00Z");
}
function shift(s, days) {
    const d = parseIso(s);
    d.setUTCDate(d.getUTCDate() + days);
    return iso(d);
}
function fmtD(s) {
    if (!s) {
        return "-";
    }
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const d = parseIso(s);
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " + d.getUTCFullYear();
}
function todayIso() {
    return iso(new Date());
}
function n(v) {
    v = +v || 0;
    return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 1 });
}

export class ManagementDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.presets = PRESETS;
        this.measureOrder = MEASURE_ORDER;
        this.dFromRef = useRef("dFrom");
        this.dToRef = useRef("dTo");
        this.paneRef = useRef("pane");
        this.rootRef = useRef("root");
        // Odoo's own dark-mode toggle (user menu) sets this cookie and reloads the page
        // (see web/views/webclient_templates.xml), independently of the OS/browser's
        // prefers-color-scheme - follow that instead of the OS setting.
        this.isDark = cookie.get("color_scheme") === "dark";
        // Coming back through the breadcrumb from a drill-down list: same window, same
        // measure, same scroll position as when the link was clicked.
        const saved = this.props.state || {};

        this.state = useState({
            loading: true,
            measures: [],
            details: {},
            thresholds: {},
            thresholdDraft: {},
            selectedKey: saved.selectedKey || "arrears",
            showLegend: false,
            showLimits: false,
            asOf: "",
            filter: saved.filter || this._presetWindow("d90"),
            copyLabel: "Copy text",
        });

        useSetupAction({
            getLocalState: () => ({
                filter: this.state.filter,
                selectedKey: this.state.selectedKey,
                scrollTop: this.rootRef.el ? this.rootRef.el.scrollTop : 0,
            }),
        });

        onWillStart(() => this.loadData());
        onMounted(() => {
            const F = this.state.filter;
            if (F.id === "custom") {
                if (this.dFromRef.el) {
                    this.dFromRef.el.value = F.from;
                }
                if (this.dToRef.el) {
                    this.dToRef.el.value = F.to;
                }
            }
            if (saved.scrollTop && this.rootRef.el) {
                this.rootRef.el.scrollTop = saved.scrollTop;
            }
        });
    }

    // ---------------------------------------------------------------- window
    _presetWindow(id) {
        const today = todayIso();
        if (id === "all") {
            return { id, all: true, from: "", to: "", fwdFrom: today, fwdTo: "2099-12-31", label: "All time" };
        }
        if (id === "mtd") {
            const from = today.slice(0, 8) + "01";
            return { id, all: false, from, to: today, fwdFrom: today, fwdTo: today, label: "This month" };
        }
        if (id === "ytd") {
            const from = today.slice(0, 4) + "-01-01";
            return { id, all: false, from, to: today, fwdFrom: today, fwdTo: today.slice(0, 4) + "-12-31", label: today.slice(0, 4) + " to date" };
        }
        const preset = PRESETS.find((p) => p.id === id) || PRESETS[3];
        return {
            id, all: false, from: shift(today, -preset.days), to: today,
            fwdFrom: today, fwdTo: shift(today, preset.days),
            label: "Last " + preset.days + " days",
        };
    }

    setPreset(id) {
        this.state.filter = this._presetWindow(id);
        this.loadData();
    }

    setCustom(from, to) {
        if (!from || !to) {
            return;
        }
        if (from > to) {
            [from, to] = [to, from];
        }
        const today = todayIso();
        this.state.filter = {
            id: "custom", all: false, from, to,
            fwdFrom: today, fwdTo: to < today ? to : shift(today, 90),
            label: fmtD(from) + " to " + fmtD(to),
        };
        this.loadData();
    }

    applyCustomRange() {
        const from = this.dFromRef.el && this.dFromRef.el.value;
        const to = this.dToRef.el && this.dToRef.el.value;
        this.setCustom(from, to);
    }

    // ---------------------------------------------------------------- data
    async loadData() {
        this.state.loading = true;
        const F = this.state.filter;
        const data = await this.orm.call("nn.management.dashboard", "get_dashboard",
            [F.from, F.to, F.fwdFrom, F.fwdTo, F.all]);
        this.state.measures = this.measureOrder
            .map((k) => data.measures.find((m) => m.key === k))
            .filter(Boolean);
        this.state.details = data.details;
        this.state.thresholds = data.thresholds;
        this.state.thresholdDraft = JSON.parse(JSON.stringify(data.thresholds));
        this.state.asOf = data.as_of;
        this.state.loading = false;
    }

    setThresholdField(key, field, value) {
        if (field === "dir") {
            this.state.thresholdDraft[key].dir = value;
        } else {
            const num = parseFloat(value);
            this.state.thresholdDraft[key][field] = isNaN(num) ? 0 : num;
        }
    }

    async applyThresholds() {
        const data = await this.orm.call("nn.management.dashboard", "set_thresholds", [this.state.thresholdDraft]);
        this.state.thresholds = data;
        await this.loadData();
    }

    async resetThresholds() {
        const data = await this.orm.call("nn.management.dashboard", "reset_thresholds", []);
        this.state.thresholds = data;
        this.state.thresholdDraft = JSON.parse(JSON.stringify(data));
        await this.loadData();
    }

    // ---------------------------------------------------------------- ui
    select(key) {
        this.state.selectedKey = key;
        this.state.copyLabel = "Copy text";
    }

    selectAll() {
        const host = this.paneRef.el;
        if (!host || !window.getSelection) {
            return;
        }
        const range = document.createRange();
        range.selectNodeContents(host);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }

    panelText() {
        const m = this.selectedMeasure;
        const d = this.selectedDetail;
        if (!m) {
            return "";
        }
        const F = this.state.filter;
        const lines = [];
        lines.push(m.idx + " · " + m.name.toUpperCase());
        if (m.action && m.action.model) {
            lines.push("Model\t" + m.action.model);
        }
        lines.push("Window\t" + F.label + (F.all ? "" : "  (" + F.from + " to " + F.to + ")"));
        lines.push("Basis\t" + m.prov + "   Status\t" + m.status);
        lines.push("Headline\t" + this.fmt(m.value) + " " + m.unit + "   " + m.vtxt);
        if (d) {
            lines.push("");
            lines.push("SUB-MEASURES");
            (d.subs || []).forEach((s) => lines.push(s.k + "\t" + s.v + "\t" + s.x));
            (d.bars || []).forEach((b) => {
                lines.push("");
                lines.push(b.title.toUpperCase() + "  (" + b.unit + ")");
                b.rows.forEach((r) => lines.push(r[0] + "\t" + this.fmt(r[2]) + (b.single ? "" : "\t" + this.fmt(r[1]))));
            });
            (d.tables || []).forEach((t) => {
                lines.push("");
                lines.push(t.title.toUpperCase());
                lines.push(t.cols.join("\t"));
                t.rows.forEach((row) => lines.push(row.join("\t")));
            });
            if (d.cav && d.cav.length) {
                lines.push("");
                lines.push("NOTE — " + d.cav[0].toUpperCase());
                d.cav.slice(1).forEach((note) => lines.push("- " + note));
            }
        }
        return lines.join("\n");
    }

    async copyText() {
        const text = this.panelText();
        try {
            await navigator.clipboard.writeText(text);
            this.state.copyLabel = "Copied";
        } catch (err) {
            this.state.copyLabel = "Could not copy — use Select all instead";
        }
        setTimeout(() => { this.state.copyLabel = "Copy text"; }, 2000);
    }

    toggleLegend() {
        this.state.showLegend = !this.state.showLegend;
        this.state.showLimits = false;
    }

    toggleLimits() {
        this.state.showLimits = !this.state.showLimits;
        this.state.showLegend = false;
    }

    open(action) {
        if (!action) {
            return;
        }
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: action.name,
            res_model: action.model,
            domain: action.domain,
            views: [[action.view_id || false, "list"], [false, "form"]],
            target: "current",
        });
    }

    get selectedMeasure() {
        return this.state.measures.find((m) => m.key === this.state.selectedKey) || this.state.measures[0];
    }

    get selectedDetail() {
        const m = this.selectedMeasure;
        return m ? this.state.details[m.key] : null;
    }

    fmt(v) {
        return n(v);
    }

    statusLabel(status) {
        return STATUS_LABEL[status] || status;
    }

    provLabel(prov) {
        return PROV_LABEL[prov] || prov;
    }

    barWidth(rows, idx, col) {
        const max = Math.max(...rows.map((r) => Math.abs(r[col])), 1);
        return Math.max(2, Math.round((Math.abs(rows[idx][col]) / max) * 100));
    }
}

ManagementDashboard.template = "nn_management_dashboard.Template";
registry.category("actions").add("nn_management_dashboard", ManagementDashboard);
