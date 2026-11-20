import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, unwrap } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDate } from "@/lib/utils";

const ROLES = ["admin", "cycle_owner", "respondent", "viewer"];

export function AdminPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">{t("admin.title")}</h1>
      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">{t("admin.users")}</TabsTrigger>
          <TabsTrigger value="servers">{t("admin.servers")}</TabsTrigger>
          <TabsTrigger value="llm">{t("admin.llm")}</TabsTrigger>
          <TabsTrigger value="plugins">{t("admin.plugins")}</TabsTrigger>
          <TabsTrigger value="audit">{t("admin.audit")}</TabsTrigger>
        </TabsList>
        <TabsContent value="users">
          <Users />
        </TabsContent>
        <TabsContent value="servers">
          <Servers />
        </TabsContent>
        <TabsContent value="llm">
          <Llm />
        </TabsContent>
        <TabsContent value="plugins">
          <Plugins />
        </TabsContent>
        <TabsContent value="audit">
          <Audit />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Users() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: async () => unwrap(await api.GET("/api/v1/users")) });
  const tenant = useQuery({ queryKey: ["tenant"], queryFn: async () => unwrap(await api.GET("/api/v1/tenant")) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const update = useMutation({
    mutationFn: async ({ id, roles, active }: { id: string; roles?: string[]; active?: boolean }) =>
      unwrap(await api.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: id } }, body: { roles, active } })),
    onSuccess: refresh,
  });
  const [form, setForm] = useState({ name: "", email: "", role: "respondent", password: "" });
  const create = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/users", { body: { name: form.name, email: form.email, roles: [form.role], password: form.password || null } })),
    onSuccess: () => {
      setForm({ name: "", email: "", role: "respondent", password: "" });
      refresh();
    },
  });
  if (users.isLoading) return <Loading />;
  return (
    <Card>
      <CardContent className="pt-4">
        <p className="mb-3 text-sm text-slate-600">
          {t("admin.tenant")}: <strong>{(tenant.data as { name?: string } | undefined)?.name}</strong>
        </p>
        <table className="w-full text-left text-sm">
          <thead className="border-b text-slate-600">
            <tr>
              <th className="py-2">Name</th>
              <th>E-mail</th>
              <th>{t("admin.roles")}</th>
              <th>{t("admin.active")}</th>
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).map((u) => (
              <tr key={u.user_id} className="border-b last:border-0">
                <td className="py-2">{u.name}</td>
                <td>{u.email}</td>
                <td className="flex flex-wrap gap-2 py-2">
                  {ROLES.map((r) => (
                    <label key={r} className="inline-flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        checked={u.roles.includes(r)}
                        onChange={(e) =>
                          update.mutate({ id: u.user_id, roles: e.target.checked ? [...u.roles, r] : u.roles.filter((x) => x !== r) })
                        }
                      />
                      {r}
                    </label>
                  ))}
                </td>
                <td>
                  <Switch checked={u.active} onCheckedChange={(v) => update.mutate({ id: u.user_id, active: v })} aria-label={`${t("admin.active")} ${u.name}`} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <form
          className="mt-4 flex flex-wrap items-end gap-3 border-t pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="u-name">Name</Label>
            <Input id="u-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="u-email">E-mail</Label>
            <Input id="u-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="u-pw">{t("login.password")}</Label>
            <Input id="u-pw" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.roles")}
            <NativeSelect value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {ROLES.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </NativeSelect>
          </label>
          <Button type="submit">{t("admin.createUser")}</Button>
        </form>
        <ErrorBox error={create.error ?? update.error} />
      </CardContent>
    </Card>
  );
}

function Servers() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: async () => unwrap(await api.GET("/api/v1/mcp-servers")) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["mcp-servers"] });
  const patch = useMutation({
    mutationFn: async ({ id, body }: { id: string; body: { enabled?: boolean; max_access_mode?: string } }) =>
      unwrap(await api.PATCH("/api/v1/mcp-servers/{server_id}", { params: { path: { server_id: id } }, body })),
    onSuccess: refresh,
  });
  const [f, setF] = useState({ name: "", endpoint: "", transport: "streamable_http", auth_type: "none", max_access_mode: "read", oauth: "{}", overrides: "{}" });
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/mcp-servers", {
          body: { name: f.name, endpoint: f.endpoint, transport: f.transport, auth_type: f.auth_type, max_access_mode: f.max_access_mode, oauth: JSON.parse(f.oauth || "{}"), tool_classification: JSON.parse(f.overrides || "{}") },
        }),
      ),
    onSuccess: refresh,
  });
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-4">
        <ul className="flex flex-col gap-2 text-sm">
          {(servers.data ?? []).map((s) => (
            <li key={s.server_id} className="flex flex-wrap items-center gap-3 rounded border p-2">
              <strong>{s.name}</strong>
              <code className="text-xs">{s.endpoint}</code>
              <Badge variant="outline">{s.transport}</Badge>
              <Badge variant="outline">{s.auth_type}</Badge>
              <label className="flex items-center gap-1">
                max
                <NativeSelect value={s.max_access_mode} onChange={(e) => patch.mutate({ id: s.server_id, body: { max_access_mode: e.target.value } })}>
                  <option value="read">read</option>
                  <option value="read_write">read_write</option>
                </NativeSelect>
              </label>
              <label className="flex items-center gap-1">
                {t("admin.enabled")}
                <Switch checked={s.enabled} onCheckedChange={(v) => patch.mutate({ id: s.server_id, body: { enabled: v } })} />
              </label>
              {Object.keys(s.tool_classification).length > 0 && <span className="text-xs">overrides: {JSON.stringify(s.tool_classification)}</span>}
            </li>
          ))}
        </ul>
        <form
          className="grid gap-3 border-t pt-4 md:grid-cols-3"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="flex flex-col gap-1">
            <Label htmlFor="s-name">Name</Label>
            <Input id="s-name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="s-endpoint">{t("admin.endpoint")}</Label>
            <Input id="s-endpoint" value={f.endpoint} onChange={(e) => setF({ ...f, endpoint: e.target.value })} required />
          </div>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.transport")}
            <NativeSelect value={f.transport} onChange={(e) => setF({ ...f, transport: e.target.value })}>
              <option value="streamable_http">streamable_http</option>
              <option value="stdio">stdio</option>
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("admin.authType")}
            <NativeSelect value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })}>
              <option value="none">none</option>
              <option value="api_key">api_key</option>
              <option value="oauth2">oauth2</option>
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            max
            <NativeSelect value={f.max_access_mode} onChange={(e) => setF({ ...f, max_access_mode: e.target.value })}>
              <option value="read">read</option>
              <option value="read_write">read_write</option>
            </NativeSelect>
          </label>
          <div className="flex flex-col gap-1">
            <Label htmlFor="s-oauth">OAuth (JSON)</Label>
            <Input id="s-oauth" value={f.oauth} onChange={(e) => setF({ ...f, oauth: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="s-over">Tool classification overrides (JSON)</Label>
            <Input id="s-over" value={f.overrides} onChange={(e) => setF({ ...f, overrides: e.target.value })} />
          </div>
          <div className="flex items-end">
            <Button type="submit">{t("admin.createServer")}</Button>
          </div>
        </form>
        <ErrorBox error={create.error ?? patch.error} />
      </CardContent>
    </Card>
  );
}

function Llm() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const tenant = useQuery({ queryKey: ["tenant"], queryFn: async () => unwrap(await api.GET("/api/v1/tenant")) });
  const current = ((tenant.data as { settings?: { llm?: { default?: { provider?: string; model?: string } } } } | undefined)?.settings?.llm?.default) ?? {};
  const [provider, setProvider] = useState<string | undefined>();
  const [model, setModel] = useState<string | undefined>();
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PATCH("/api/v1/tenant/settings", {
          body: { settings: { llm: { default: { provider: provider ?? current.provider ?? "anthropic", model: model ?? current.model ?? "claude-opus-5-5" } } } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tenant"] }),
  });
  return (
    <Card>
      <CardContent className="flex flex-wrap items-end gap-3 pt-4">
        <label className="flex flex-col gap-1 text-sm">
          {t("admin.provider")}
          <NativeSelect value={provider ?? current.provider ?? "anthropic"} onChange={(e) => setProvider(e.target.value)}>
            <option value="anthropic">anthropic</option>
            <option value="openai_compatible">openai_compatible</option>
            <option value="fake">fake</option>
          </NativeSelect>
        </label>
        <div className="flex flex-col gap-1">
          <Label htmlFor="llm-model">{t("admin.model")}</Label>
          <Input id="llm-model" value={model ?? current.model ?? "claude-opus-5-5"} onChange={(e) => setModel(e.target.value)} />
        </div>
        <Button onClick={() => save.mutate()}>{t("admin.saveLlm")}</Button>
        <ErrorBox error={save.error} />
        <p className="w-full text-xs text-slate-600">
          Tenant default for all AgentDefinitions; API keys are configured in the environment (DP_AC_ANTHROPIC_API_KEY, DP_AC_OPENAI_API_KEY,
          DP_AC_OPENAI_BASE_URL).
        </p>
      </CardContent>
    </Card>
  );
}

function Plugins() {
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: async () => unwrap(await api.GET("/api/v1/plugins")) });
  const rows = (plugins.data as { extensions?: { point: string; name: string; origin: string; interface_version: string | null }[] } | undefined)?.extensions ?? [];
  return (
    <Card>
      <CardContent className="pt-4">
        <table className="w-full text-left text-sm">
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.point}-${r.name}`} className="border-b last:border-0">
                <td className="py-1 font-mono text-xs">{r.point}</td>
                <td>{r.name}</td>
                <td>v{r.interface_version ?? "—"}</td>
                <td className="text-xs text-slate-600">{r.origin}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function Audit() {
  const audit = useQuery({ queryKey: ["audit"], queryFn: async () => unwrap(await api.GET("/api/v1/audit", { params: { query: { limit: 200 } } })) });
  return (
    <Card>
      <CardContent className="pt-4">
        <ul className="flex flex-col gap-1 text-xs">
          {(audit.data ?? []).map((e) => (
            <li key={e.audit_id}>
              {formatDate(e.at)} · {e.actor_id ?? "—"} · <code>{e.action}</code> · {e.resource} · {e.outcome}
              {e.agent_run_id ? ` · agent ${e.agent_run_id}` : ""}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
