import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, unwrap, type Schemas } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/lib/utils";

type Server = Schemas["McpServerOut"];

export function ConnectionsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: async () => unwrap(await api.GET("/api/v1/mcp-servers")) });
  const cons = useQuery({ queryKey: ["connections"], queryFn: async () => unwrap(await api.GET("/api/v1/connections")) });
  const audit = useQuery({
    queryKey: ["audit", "mine"],
    queryFn: async () => unwrap(await api.GET("/api/v1/audit", { params: { query: { mine: true, limit: 50 } } })),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["connections"] });
  const setMode = useMutation({
    mutationFn: async ({ id, mode }: { id: string; mode: string }) =>
      unwrap(await api.PATCH("/api/v1/connections/{connection_id}", { params: { path: { connection_id: id } }, body: { access_mode: mode } })),
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: async (id: string) => unwrap(await api.DELETE("/api/v1/connections/{connection_id}", { params: { path: { connection_id: id } } })),
    onSuccess: refresh,
  });
  if (servers.isLoading || cons.isLoading) return <Loading />;
  const byId = Object.fromEntries((servers.data ?? []).map((s) => [s.server_id, s]));
  const live = (cons.data ?? []).filter((c) => c.status !== "revoked");
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">{t("connections.title")}</h1>
      <ErrorBox error={servers.error ?? cons.error ?? setMode.error ?? revoke.error} />
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>{t("connections.title")}</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {live.length === 0 && <p className="text-sm text-slate-600">{t("connections.none")}</p>}
          <ul className="flex flex-col gap-2" data-testid="connections">
            {live.map((c) => (
              <li key={c.connection_id} className="flex flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
                <strong>{byId[c.server_id]?.name ?? c.server_id}</strong>
                <StateBadge state={c.status} />
                <label className="flex items-center gap-1">
                  {t("connections.accessMode")}
                  <NativeSelect
                    value={c.access_mode}
                    onChange={(e) => setMode.mutate({ id: c.connection_id, mode: e.target.value })}
                    aria-label={t("connections.accessMode")}
                  >
                    <option value="read">{t("connections.read")}</option>
                    <option value="read_write" disabled={byId[c.server_id]?.max_access_mode !== "read_write"}>
                      {t("connections.read_write")}
                    </option>
                  </NativeSelect>
                </label>
                <span className="text-xs text-slate-600">
                  {t("connections.lastUsed")}: {formatDate(c.last_used_at)}
                </span>
                <Button size="sm" variant="outline" className="ml-auto" onClick={() => revoke.mutate(c.connection_id)}>
                  {t("connections.revoke")}
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>{t("connections.available")}</h2>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {(servers.data ?? []).map((s) => (
            <ConnectForm key={s.server_id} server={s} connected={live.some((c) => c.server_id === s.server_id)} onDone={refresh} />
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>
            <h2>{t("connections.audit")}</h2>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-1 text-xs">
            {(audit.data ?? []).map((e) => (
              <li key={e.audit_id}>
                {formatDate(e.at)} · <code>{e.action}</code> · {e.resource} · <Badge variant={e.outcome === "ok" ? "secondary" : "danger"}>{e.outcome}</Badge>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function ConnectForm({ server, connected, onDone }: { server: Server; connected: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const [mode, setMode] = useState("read");
  const [key, setKey] = useState("");
  const connect = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/connections", { body: { server_id: server.server_id, access_mode: mode, api_key: key || null } })),
    onSuccess: (con) => {
      if (con.authorization_url) window.location.href = con.authorization_url;
      else onDone();
    },
  });
  const keyId = `key-${server.server_id}`;
  return (
    <form
      className="flex flex-wrap items-end gap-3 rounded-md border p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        connect.mutate();
      }}
      data-testid={`connect-${server.name}`}
    >
      <div className="min-w-48">
        <strong>{server.name}</strong>
        <p className="text-xs text-slate-600">{server.description}</p>
        <p className="text-xs text-slate-600">
          {t("connections.maxMode")}: {t(`connections.${server.max_access_mode}`)} · {server.auth_type}
        </p>
      </div>
      {server.auth_type === "api_key" && (
        <div className="flex flex-col gap-1">
          <Label htmlFor={keyId}>{t("connections.apiKey")}</Label>
          <Input id={keyId} type="password" value={key} onChange={(e) => setKey(e.target.value)} required />
        </div>
      )}
      <label className="flex flex-col gap-1">
        {t("connections.accessMode")}
        <NativeSelect value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="read">{t("connections.read")}</option>
          {server.max_access_mode === "read_write" && <option value="read_write">{t("connections.read_write")}</option>}
        </NativeSelect>
      </label>
      <Button type="submit" disabled={connected || connect.isPending}>
        {t("connections.connect")}
      </Button>
      <ErrorBox error={connect.error} />
    </form>
  );
}
