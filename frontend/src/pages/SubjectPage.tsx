import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import { useParams } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { hasRole, useMe } from "@/lib/auth";
import { formatDate } from "@/lib/utils";

const MonacoDiff = lazy(() => import("@/components/MonacoDiff"));

type Header = { version: number; status: string; based_on: number | null; created: string; change_reason: string; contributor: { user_id: string; agent_run_id?: string | null }; content_hash: string; sources: Record<string, unknown>[] };

export default function SubjectPage() {
  const { subjectId = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const p = { params: { path: { subject_id: subjectId } } };
  const subject = useQuery({ queryKey: ["subject", subjectId], queryFn: async () => unwrap(await api.GET("/api/v1/subjects/{subject_id}", p)) });
  const latest = useQuery({
    queryKey: ["subject", subjectId, "latest"],
    queryFn: async () => unwrap(await api.GET("/api/v1/subjects/{subject_id}/artifact", p)),
    retry: false,
  });
  const history = useQuery({ queryKey: ["subject", subjectId, "history"], queryFn: async () => unwrap(await api.GET("/api/v1/subjects/{subject_id}/history", p)) });
  const attachments = useQuery({
    queryKey: ["subject", subjectId, "attachments"],
    queryFn: async () => unwrap(await api.GET("/api/v1/subjects/{subject_id}/attachments", p)),
  });
  const approve = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/subjects/{subject_id}/approve", { ...p, body: { comment: "" } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["subject", subjectId] }),
  });
  const versions = (history.data ?? []).map((v) => (v.header as unknown as Header).version);
  const [from, setFrom] = useState<string>("");
  const [to, setTo] = useState<string>("latest");
  const [diffKind, setDiffKind] = useState<"factsheet" | "data">("factsheet");
  const diff = useQuery({
    queryKey: ["subject", subjectId, "diff", from, to],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/subjects/{subject_id}/diff", { params: { path: { subject_id: subjectId }, query: { from_version: from, to_version: to } } })),
    enabled: !!from,
  });
  const fromData = history.data?.find((v) => String((v.header as unknown as Header).version) === from)?.data;
  const toData = to === "latest" ? latest.data?.data : history.data?.find((v) => String((v.header as unknown as Header).version) === to)?.data;

  if (subject.isLoading) return <Loading />;
  if (subject.error) return <ErrorBox error={subject.error} />;
  const h = latest.data?.header as unknown as Header | undefined;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{subject.data!.name}</h1>
        {h && <StateBadge state={h.status} />}
        {h && (
          <span className="text-sm text-slate-600">
            v{h.version} · {formatDate(h.created)}
          </span>
        )}
        {latest.data && (
          <Badge variant={latest.data.integrity_ok ? "success" : "danger"}>
            {t("subject.integrity")}: {latest.data.integrity_ok ? "ok" : "!"}
          </Badge>
        )}
        {hasRole(me, "admin", "cycle_owner") && h?.status === "validated" && (
          <Button onClick={() => approve.mutate()} data-testid="approve">
            {t("subject.approve")}
          </Button>
        )}
      </div>
      <ErrorBox error={approve.error} />
      <Tabs defaultValue="factsheet">
        <TabsList>
          <TabsTrigger value="factsheet">{t("subject.factsheet")}</TabsTrigger>
          <TabsTrigger value="data">{t("subject.data")}</TabsTrigger>
          <TabsTrigger value="history">{t("subject.history")}</TabsTrigger>
          <TabsTrigger value="diff">{t("subject.diff")}</TabsTrigger>
          <TabsTrigger value="attachments">{t("subject.attachments")}</TabsTrigger>
        </TabsList>
        <TabsContent value="factsheet">
          <Card>
            <CardContent className="prose prose-sm max-w-none pt-4 [&_table]:w-full [&_td]:border [&_td]:px-2 [&_th]:border [&_th]:px-2" data-testid="factsheet">
              {latest.data ? <Markdown>{latest.data.factsheet}</Markdown> : <p>—</p>}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="data">
          <Card>
            <CardContent className="pt-4">
              <pre className="overflow-x-auto text-xs">{JSON.stringify(latest.data ? { header: latest.data.header, data: latest.data.data } : {}, null, 2)}</pre>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="history">
          <Card>
            <CardContent className="pt-4">
              <ol className="flex flex-col gap-2 text-sm">
                {(history.data ?? []).map((v) => {
                  const hh = v.header as unknown as Header;
                  return (
                    <li key={hh.version} className="rounded border p-2">
                      <strong>v{hh.version}</strong> <StateBadge state={hh.status} /> · {formatDate(hh.created)} · {hh.change_reason}
                      <div className="text-xs text-slate-600">
                        based on: {hh.based_on ?? "—"} · contributor: {hh.contributor.user_id}
                        {hh.contributor.agent_run_id ? ` (agent run ${hh.contributor.agent_run_id})` : ""} · {hh.sources.length} sources
                      </div>
                    </li>
                  );
                })}
              </ol>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="diff">
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-1">
                  {t("subject.from")}
                  <NativeSelect value={from} onChange={(e) => setFrom(e.target.value)} data-testid="diff-from">
                    <option value="">—</option>
                    {versions.map((v) => (
                      <option key={v} value={String(v)}>
                        v{v}
                      </option>
                    ))}
                  </NativeSelect>
                </label>
                <label className="flex items-center gap-1">
                  {t("subject.to")}
                  <NativeSelect value={to} onChange={(e) => setTo(e.target.value)}>
                    <option value="latest">latest</option>
                    {versions.map((v) => (
                      <option key={v} value={String(v)}>
                        v{v}
                      </option>
                    ))}
                  </NativeSelect>
                </label>
                <NativeSelect value={diffKind} onChange={(e) => setDiffKind(e.target.value as "factsheet" | "data")} aria-label="diff kind">
                  <option value="factsheet">{t("subject.factsheet")}</option>
                  <option value="data">{t("subject.data")}</option>
                </NativeSelect>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {diff.data && (
                <>
                  <ul className="mb-3 text-sm" data-testid="field-changes">
                    {diff.data.field_changes.map((c) => (
                      <li key={String(c.path)}>
                        <code>{String(c.path)}</code> {String(c.change)}: {JSON.stringify(c.old)} → {JSON.stringify(c.new)}
                      </li>
                    ))}
                  </ul>
                  <Suspense fallback={<Loading />}>
                    <MonacoDiff
                      language={diffKind === "data" ? "json" : "markdown"}
                      original={diffKind === "data" ? JSON.stringify(fromData ?? {}, null, 2) : (diff.data.from_factsheet ?? "")}
                      modified={diffKind === "data" ? JSON.stringify(toData ?? {}, null, 2) : (diff.data.to_factsheet ?? "")}
                    />
                  </Suspense>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="attachments">
          <Card>
            <CardContent className="pt-4">
              <ul className="text-sm">
                {(attachments.data ?? []).map((a) => (
                  <li key={a.attachment_id}>
                    <a className="underline-offset-4 hover:underline" href={`/api/v1/attachments/${a.attachment_id}/content`}>
                      {a.filename}
                    </a>{" "}
                    <StateBadge state={a.visibility} />
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
