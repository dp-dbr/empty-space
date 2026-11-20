import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, uploadFiles, type Schemas } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useMe } from "@/lib/auth";
import { cn, formatDate } from "@/lib/utils";

type Message = Schemas["Message"];
type Proposal = Schemas["FactProposal"];
type Action = Schemas["PendingAction"];
type Attachment = Schemas["AttachmentOut"];
type Draft = {
  base_version: number | null;
  data: Record<string, unknown>;
  confirmed: string[];
  pending: { proposal_id: string; path: string; value: unknown }[];
  conflict: null | {
    latest_version: number;
    conflicts: { path: string; base: unknown; mine: unknown; theirs: unknown }[];
  };
};

const fmt = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

export function JobPage() {
  const { jobId = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["job", jobId] });

  const job = useQuery({
    queryKey: ["job", jobId, "job"],
    queryFn: async () => unwrap(await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } } })),
  });
  const messages = useQuery({
    queryKey: ["job", jobId, "messages"],
    queryFn: async () => unwrap(await api.GET("/api/v1/jobs/{job_id}/messages", { params: { path: { job_id: jobId } } })),
    refetchInterval: 1500,
  });
  const proposals = useQuery({
    queryKey: ["job", jobId, "proposals"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs/{job_id}/proposals", { params: { path: { job_id: jobId }, query: { status: "proposed" } } })),
    refetchInterval: 1500,
  });
  const actions = useQuery({
    queryKey: ["job", jobId, "actions"],
    queryFn: async () => unwrap(await api.GET("/api/v1/jobs/{job_id}/pending-actions", { params: { path: { job_id: jobId } } })),
    refetchInterval: 1500,
  });
  const draft = useQuery({
    queryKey: ["job", jobId, "draft"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs/{job_id}/draft", { params: { path: { job_id: jobId } } })) as unknown as Draft,
    refetchInterval: 3000,
  });
  const attachments = useQuery({
    queryKey: ["job", jobId, "attachments"],
    queryFn: async () => unwrap(await api.GET("/api/v1/jobs/{job_id}/attachments", { params: { path: { job_id: jobId } } })),
    refetchInterval: 3000,
  });

  if (job.isLoading) return <Loading />;
  if (job.error) return <ErrorBox error={job.error} />;
  const pendingActions = (actions.data ?? []).filter((a) => a.status === "pending");

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <section aria-labelledby="conv-title" className="flex flex-col gap-4">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle id="conv-title">
              <h1>{t("jobs.subject")}</h1>
            </CardTitle>
            <div className="flex items-center gap-2 text-sm">
              <StateBadge state={job.data!.state} />
              <Link className="underline-offset-4 hover:underline" to={`/subjects/${job.data!.subject_id}`}>
                {t("subject.history")}
              </Link>
            </div>
          </CardHeader>
          <CardContent>
            <Conversation messages={messages.data ?? []} />
            <Composer jobId={jobId} onSent={refresh} />
          </CardContent>
        </Card>
        {pendingActions.length > 0 && <WriteConfirmations actions={pendingActions} onDone={refresh} />}
        <ProposalsPanel proposals={proposals.data ?? []} onDone={refresh} />
      </section>
      <aside className="flex flex-col gap-4">
        <DraftPanel jobId={jobId} draft={draft.data} onDone={refresh} />
        <AttachmentsPanel jobId={jobId} items={attachments.data ?? []} onDone={refresh} />
      </aside>
    </div>
  );
}

function Conversation({ messages }: { messages: Message[] }) {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const last = messages[messages.length - 1];
  const waiting = last && last.sender.kind === "user";
  return (
    <ol aria-live="polite" aria-label="Conversation" className="mb-3 flex max-h-[55vh] flex-col gap-2 overflow-y-auto" data-testid="messages">
      {messages.map((m) => {
        const mine = m.sender.kind === "user" && m.sender.ref === me?.user.user_id;
        return (
          <li
            key={m.message_id}
            data-kind={m.kind}
            className={cn(
              "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
              mine ? "self-end bg-slate-900 text-white" : "self-start bg-slate-100",
              m.kind === "validation" && "border border-amber-300 bg-amber-50",
              m.kind === "conflict" && "border border-red-300 bg-red-50",
              m.kind === "notice" && "bg-blue-50",
            )}
          >
            {m.kind !== "text" && (
              <Badge variant="outline" className="mb-1">
                {m.kind.replace("_", " ")}
              </Badge>
            )}
            <div>{m.text}</div>
            {m.kind === "validation" && Array.isArray((m.payload as { violations?: unknown[] }).violations) && (
              <ul className="mt-1 list-disc pl-4 text-xs">
                {((m.payload as { violations: { path: string; message: string }[] }).violations).map((v, i) => (
                  <li key={i}>
                    <code>{v.path}</code>: {v.message}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-1 text-[10px] opacity-60">{formatDate(m.created_at)}</div>
          </li>
        );
      })}
      {waiting && (
        <li role="status" className="self-start text-xs text-slate-500">
          {t("chat.thinking")}
        </li>
      )}
    </ol>
  );
}

function Composer({ jobId, onSent }: { jobId: string; onSent: () => void }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/jobs/{job_id}/messages", { params: { path: { job_id: jobId } }, body: { text, attachment_ids: [] } })),
    onSuccess: () => {
      setText("");
      onSent();
    },
  });
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) send.mutate();
      }}
    >
      <Label htmlFor="composer" className="sr-only">
        {t("chat.placeholder")}
      </Label>
      <Textarea
        id="composer"
        value={text}
        placeholder={t("chat.placeholder")}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) send.mutate();
        }}
      />
      <ErrorBox error={send.error} />
      <div className="flex justify-end">
        <Button type="submit" disabled={send.isPending || !text.trim()}>
          {t("chat.send")}
        </Button>
      </div>
    </form>
  );
}

function ProposalsPanel({ proposals, onDone }: { proposals: Proposal[]; onDone: () => void }) {
  const { t } = useTranslation();
  if (proposals.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>{t("chat.proposals")}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-3" data-testid="proposals">
          {proposals.map((p) => (
            <ProposalItem key={p.proposal_id} p={p} onDone={onDone} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ProposalItem({ p, onDone }: { p: Proposal; onDone: () => void }) {
  const { t } = useTranslation();
  const [ack, setAck] = useState(false);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(fmt(p.value));
  const decide = useMutation({
    mutationFn: async (decision: "confirm" | "reject" | "edit") => {
      let v: unknown = value;
      try {
        v = JSON.parse(value);
      } catch {
        /* keep string */
      }
      return unwrap(
        await api.POST("/api/v1/proposals/{proposal_id}/decision", {
          params: { path: { proposal_id: p.proposal_id ?? "" } },
          body: { decision, value: decision === "edit" ? v : undefined, visibility_ack: ack },
        }),
      );
    },
    onSuccess: onDone,
  });
  const ackId = `ack-${p.proposal_id}`;
  return (
    <li className="rounded-md border border-slate-200 p-3" data-path={p.path}>
      <div className="flex flex-wrap items-baseline gap-2">
        <code className="font-semibold">{p.path}</code>
        {editing ? (
          <Input aria-label={p.path} value={value} onChange={(e) => setValue(e.target.value)} className="max-w-xs" />
        ) : (
          <span>{fmt(p.value)}</span>
        )}
      </div>
      {p.rationale && <p className="mt-1 text-xs text-slate-600">{p.rationale}</p>}
      {(p.sources ?? []).length > 0 && (
        <p className="mt-1 text-xs text-slate-600">
          {t("chat.sources")}:{" "}
          {(p.sources ?? [])
            .map((s) => {
              const src = s as Record<string, string>;
              return src.kind === "mcp" ? `${src.server} / ${src.tool}` : src.kind === "attachment" ? `${t("chat.attachments")} (${src.content_hash?.slice(0, 19)}…)` : src.kind;
            })
            .join(", ")}
        </p>
      )}
      {p.requires_visibility_confirmation && (
        <div className="mt-2 flex items-start gap-2 rounded bg-amber-50 p-2 text-xs">
          <input id={ackId} type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
          <label htmlFor={ackId}>{t("chat.visibilityAck")}</label>
        </div>
      )}
      <div className="mt-2 flex gap-2">
        {editing ? (
          <Button size="sm" onClick={() => decide.mutate("edit")}>
            {t("chat.save")}
          </Button>
        ) : (
          <Button size="sm" onClick={() => decide.mutate("confirm")} disabled={p.requires_visibility_confirmation && !ack}>
            {t("chat.confirm")}
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => setEditing(!editing)}>
          {t("chat.edit")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => decide.mutate("reject")}>
          {t("chat.reject")}
        </Button>
      </div>
      <ErrorBox error={decide.error} />
    </li>
  );
}

function WriteConfirmations({ actions, onDone }: { actions: Action[]; onDone: () => void }) {
  const { t } = useTranslation();
  const decide = useMutation({
    mutationFn: async ({ id, confirm }: { id: string; confirm: boolean }) =>
      unwrap(await api.POST("/api/v1/pending-actions/{action_id}/decision", { params: { path: { action_id: id } }, body: { confirm } })),
    onSuccess: onDone,
  });
  return (
    <Card className="border-amber-300">
      <CardHeader>
        <CardTitle>
          <h2>{t("chat.writeConfirm")}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3" data-testid="write-confirmations">
        {actions.map((a) => (
          <div key={a.action_id} className="rounded-md bg-amber-50 p-3 text-sm">
            <p>
              <strong>{a.target}</strong>
            </p>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(a.arguments, null, 2)}</pre>
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={() => decide.mutate({ id: a.action_id ?? "", confirm: true })}>
                {t("chat.allow")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => decide.mutate({ id: a.action_id ?? "", confirm: false })}>
                {t("chat.deny")}
              </Button>
            </div>
          </div>
        ))}
        <ErrorBox error={decide.error} />
      </CardContent>
    </Card>
  );
}

function DraftPanel({ jobId, draft, onDone }: { jobId: string; draft?: Draft; onDone: () => void }) {
  const { t } = useTranslation();
  const [choices, setChoices] = useState<Record<string, "mine" | "theirs">>({});
  const submit = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/jobs/{job_id}/submit", { params: { path: { job_id: jobId } }, body: { change_reason: "submitted in web UI" } })),
    onSuccess: onDone,
  });
  const saveDraft = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/jobs/{job_id}/draft", { params: { path: { job_id: jobId } } })),
    onSuccess: onDone,
  });
  const resolve = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/jobs/{job_id}/resolve-conflict", {
          params: { path: { job_id: jobId } },
          body: { resolutions: choices, change_reason: "submitted after conflict resolution" },
        }),
      ),
    onSuccess: onDone,
  });
  if (!draft) return <Loading />;
  const entries = Object.entries(draft.data);
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>{t("chat.draft")}</h2>
        </CardTitle>
        {draft.base_version != null && (
          <p className="text-xs text-slate-600">
            {t("subject.version")} {draft.base_version}
          </p>
        )}
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm" data-testid="draft">
          {entries.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="font-mono text-xs text-slate-600">{k}</dt>
              <dd>{fmt(v)}</dd>
            </div>
          ))}
        </dl>
        {draft.conflict && (
          <div className="mt-3 rounded-md border border-red-300 bg-red-50 p-3 text-sm" data-testid="conflict">
            <h3 className="font-semibold">{t("chat.conflict")}</h3>
            <p className="text-xs">{t("chat.conflictHelp")}</p>
            {draft.conflict.conflicts.map((c) => (
              <fieldset key={c.path} className="mt-2">
                <legend className="font-mono text-xs">{c.path}</legend>
                {(["mine", "theirs"] as const).map((side) => (
                  <label key={side} className="mr-3 inline-flex items-center gap-1 text-xs">
                    <input
                      type="radio"
                      name={`c-${c.path}`}
                      checked={(choices[c.path] ?? "theirs") === side}
                      onChange={() => setChoices({ ...choices, [c.path]: side })}
                    />
                    {t(`chat.${side}`)}: {fmt(c[side])}
                  </label>
                ))}
              </fieldset>
            ))}
            <Button size="sm" className="mt-2" onClick={() => resolve.mutate()}>
              {t("chat.resolve")}
            </Button>
            <ErrorBox error={resolve.error} />
          </div>
        )}
        <div className="mt-3 flex gap-2">
          <Button onClick={() => submit.mutate()} disabled={submit.isPending || entries.length === 0}>
            {t("chat.submit")}
          </Button>
          <Button variant="outline" onClick={() => saveDraft.mutate()} disabled={saveDraft.isPending || entries.length === 0}>
            {t("chat.saveDraft")}
          </Button>
        </div>
        {submit.data?.version && <p className="mt-2 text-sm text-green-800">{t("chat.submitted")} (v{String(submit.data.version.header.version)})</p>}
        <ErrorBox error={submit.error ?? saveDraft.error} />
      </CardContent>
    </Card>
  );
}

function AttachmentsPanel({ jobId, items, onDone }: { jobId: string; items: Attachment[]; onDone: () => void }) {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const [shared, setShared] = useState(false);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const atts = await uploadFiles(jobId, files, shared ? "shared" : "private");
      await api.POST("/api/v1/jobs/{job_id}/messages", {
        params: { path: { job_id: jobId } },
        body: { text: `Please use the attached file(s): ${atts.map((a) => a.filename).join(", ")}`, attachment_ids: atts.map((a) => a.attachment_id) },
      });
      return atts;
    },
    onSuccess: onDone,
  });
  const act = useMutation({
    mutationFn: async ({ id, op }: { id: string; op: "share" | "withdraw" | "delete" }) => {
      if (op === "delete") return unwrap(await api.DELETE("/api/v1/attachments/{attachment_id}", { params: { path: { attachment_id: id } } }));
      const path = op === "share" ? "/api/v1/attachments/{attachment_id}/share" : "/api/v1/attachments/{attachment_id}/withdraw";
      return unwrap(await api.POST(path, { params: { path: { attachment_id: id } } }));
    },
    onSuccess: onDone,
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2>{t("chat.attachments")}</h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            const files = Array.from(e.dataTransfer.files);
            if (files.length) upload.mutate(files);
          }}
          className={cn("rounded-md border-2 border-dashed p-4 text-center text-sm text-slate-600", drag ? "border-slate-900 bg-slate-100" : "border-slate-300")}
        >
          <p>{t("chat.dropHere")}</p>
          <input
            ref={input}
            type="file"
            multiple
            className="sr-only"
            aria-label={t("chat.chooseFiles")}
            data-testid="file-input"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) upload.mutate(files);
              e.target.value = "";
            }}
          />
          <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => input.current?.click()}>
            {t("chat.chooseFiles")}
          </Button>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Switch id="share-switch" checked={shared} onCheckedChange={setShared} aria-label={t("chat.visibility")} />
          <Label htmlFor="share-switch">{shared ? t("chat.shared") : t("chat.private")}</Label>
        </div>
        <p className="text-xs text-slate-600">{t("chat.uploadHint")}</p>
        <ErrorBox error={upload.error ?? act.error} />
        <ul className="flex flex-col gap-2 text-sm" data-testid="attachments">
          {items.map((a) => {
            const own = a.uploaded_by === me?.user.user_id;
            return (
              <li key={a.attachment_id} className="flex flex-wrap items-center gap-2">
                <a className="underline-offset-4 hover:underline" href={`/api/v1/attachments/${a.attachment_id}/content`}>
                  {a.filename}
                </a>
                <StateBadge state={a.visibility} />
                {a.status !== "active" && <StateBadge state={a.status} />}
                {own && a.status === "active" && a.visibility === "private" && (
                  <>
                    <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: a.attachment_id, op: "share" })}>
                      {t("chat.share")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: a.attachment_id, op: "delete" })}>
                      {t("chat.delete")}
                    </Button>
                  </>
                )}
                {own && a.status === "active" && a.visibility === "shared" && (
                  <Button size="sm" variant="ghost" onClick={() => act.mutate({ id: a.attachment_id, op: "withdraw" })}>
                    {t("chat.withdraw")}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
