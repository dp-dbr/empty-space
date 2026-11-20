import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { Badge, StateBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, NativeSelect } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { hasRole, useMe } from "@/lib/auth";
import { formatDate } from "@/lib/utils";

export function CyclesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const cycles = useQuery({ queryKey: ["cycles"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles")) });
  const [name, setName] = useState("");
  const [due, setDue] = useState("");
  const [previous, setPrevious] = useState("");
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/cycles", {
          body: {
            name,
            subject_type: "application",
            due_date: due ? new Date(due).toISOString() : null,
            previous_cycle_id: previous || null,
            recurrence: previous ? "recurring" : "one_off",
          },
        }),
      ),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["cycles"] });
    },
  });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">{t("cycles.title")}</h1>
      {hasRole(me, "admin", "cycle_owner") && (
        <Card>
          <CardHeader>
            <CardTitle>
              <h2>{t("cycles.new")}</h2>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                create.mutate();
              }}
            >
              <div className="flex flex-col gap-1">
                <Label htmlFor="cycle-name">{t("cycles.name")}</Label>
                <Input id="cycle-name" value={name} onChange={(e) => setName(e.target.value)} required placeholder="app-inventory-2027-Q1" />
              </div>
              <div className="flex flex-col gap-1">
                <Label htmlFor="cycle-due">{t("cycles.due")}</Label>
                <Input id="cycle-due" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
              </div>
              <label className="flex flex-col gap-1 text-sm">
                {t("cycles.previous")}
                <NativeSelect value={previous} onChange={(e) => setPrevious(e.target.value)}>
                  <option value="">{t("cycles.none")}</option>
                  {(cycles.data ?? []).map((c) => (
                    <option key={c.cycle_id} value={c.cycle_id}>
                      {c.name}
                    </option>
                  ))}
                </NativeSelect>
              </label>
              <Button type="submit">{t("cycles.create")}</Button>
            </form>
            <ErrorBox error={create.error} />
          </CardContent>
        </Card>
      )}
      {cycles.isLoading && <Loading />}
      <ul className="grid gap-3 md:grid-cols-2">
        {(cycles.data ?? []).map((c) => (
          <li key={c.cycle_id}>
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>
                  <Link to={`/cycles/${c.cycle_id}`} className="underline-offset-4 hover:underline">
                    {c.name}
                  </Link>
                </CardTitle>
                <StateBadge state={c.status} />
              </CardHeader>
              <CardContent className="text-sm text-slate-600">
                {c.subject_type} · {t("cycles.due")}: {formatDate(c.due_date)}
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Consolidation = {
  subjects: number;
  with_data: number;
  coverage: Record<string, number>;
  gaps: { check: string; subjects: string[]; path: string; message: string }[];
  contradictions: { check: string; subjects: string[]; path: string; message: string }[];
};

export function CycleDetailPage() {
  const { cycleId = "" } = useParams();
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const p = { params: { path: { cycle_id: cycleId } } };
  const cycle = useQuery({ queryKey: ["cycle", cycleId], queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}", p)) });
  const subjects = useQuery({ queryKey: ["cycle", cycleId, "subjects"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/subjects", p)) });
  const jobs = useQuery({ queryKey: ["cycle", cycleId, "jobs"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/jobs", p)), refetchInterval: 4000 });
  const status = useQuery({ queryKey: ["cycle", cycleId, "status"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/status", p)), refetchInterval: 4000 });
  const snapshots = useQuery({ queryKey: ["cycle", cycleId, "snapshots"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/snapshots", p)) });
  const allCycles = useQuery({ queryKey: ["cycles"], queryFn: async () => unwrap(await api.GET("/api/v1/cycles")) });
  const owner = hasRole(me, "admin", "cycle_owner");
  const users = useQuery({ queryKey: ["users"], queryFn: async () => unwrap(await api.GET("/api/v1/users")), enabled: owner });
  const consolidation = useQuery({
    queryKey: ["cycle", cycleId, "consolidation"],
    queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/consolidation", p)) as unknown as Consolidation,
    enabled: owner,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["cycle", cycleId] });
  const start = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/cycles/{cycle_id}/start", p)), onSuccess: refresh });
  const init = useMutation({ mutationFn: async () => unwrap(await api.POST("/api/v1/cycles/{cycle_id}/init-from-previous", p)), onSuccess: refresh });
  const [subjectName, setSubjectName] = useState("");
  const [assignee, setAssignee] = useState("");
  const add = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/cycles/{cycle_id}/subjects", { ...p, body: { name: subjectName, assignee_id: assignee } })),
    onSuccess: () => {
      setSubjectName("");
      refresh();
    },
  });
  const [snapName, setSnapName] = useState("");
  const snap = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/cycles/{cycle_id}/snapshots", { ...p, body: { name: snapName } })),
    onSuccess: refresh,
  });
  const [compareRef, setCompareRef] = useState("");
  const compare = useQuery({
    queryKey: ["cycle", cycleId, "compare", compareRef],
    queryFn: async () => unwrap(await api.GET("/api/v1/cycles/{cycle_id}/compare", { params: { path: { cycle_id: cycleId }, query: { snapshot: compareRef } } })),
    enabled: !!compareRef,
  });
  const previousSnaps = useQuery({
    queryKey: ["cycle", cycle.data?.previous_cycle_id, "snapshots"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/cycles/{cycle_id}/snapshots", { params: { path: { cycle_id: cycle.data!.previous_cycle_id! } } })),
    enabled: !!cycle.data?.previous_cycle_id,
  });

  if (cycle.isLoading) return <Loading />;
  if (cycle.error) return <ErrorBox error={cycle.error} />;
  const c = cycle.data!;
  const subjectName_ = Object.fromEntries((subjects.data ?? []).map((s) => [s.subject_id, s.name]));
  const userName = Object.fromEntries((users.data ?? []).map((u) => [u.user_id, u.name]));
  const snapOptions = [
    ...(snapshots.data ?? []).map((s) => `${s.cycle_id}/${s.name}`),
    ...(previousSnaps.data ?? []).map((s) => `${s.cycle_id}/${s.name}`),
  ];
  const cycleName = (id: string) => allCycles.data?.find((x) => x.cycle_id === id)?.name ?? id;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">{c.name}</h1>
        <StateBadge state={c.status} />
        {owner && c.status === "planned" && <Button onClick={() => start.mutate()}>{t("cycles.start")}</Button>}
        {owner && c.previous_cycle_id && (subjects.data ?? []).length === 0 && (
          <Button variant="outline" onClick={() => init.mutate()}>
            {t("cycles.initPrevious")}
          </Button>
        )}
      </div>
      <ErrorBox error={start.error ?? init.error ?? add.error ?? snap.error} />
      {status.data && (
        <div className="flex flex-wrap gap-2 text-sm" data-testid="cycle-status">
          {Object.entries((status.data as { jobs: Record<string, number> }).jobs).map(([k, v]) => (
            <Badge key={k} variant="outline">
              {k.replace("_", " ")}: {v}
            </Badge>
          ))}
        </div>
      )}
      <Tabs defaultValue="jobs">
        <TabsList>
          <TabsTrigger value="jobs">{t("cycles.jobs")}</TabsTrigger>
          <TabsTrigger value="snapshots">{t("cycles.snapshots")}</TabsTrigger>
          {owner && <TabsTrigger value="consolidation">{t("cycles.consolidation")}</TabsTrigger>}
        </TabsList>
        <TabsContent value="jobs">
          <Card>
            <CardContent className="pt-4">
              <table className="w-full text-left text-sm">
                <thead className="border-b text-slate-600">
                  <tr>
                    <th className="py-2">{t("jobs.subject")}</th>
                    <th>{t("cycles.assignee")}</th>
                    <th>{t("jobs.state")}</th>
                    <th>{t("subject.version")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(jobs.data ?? []).map((j) => (
                    <tr key={j.job_id} className="border-b last:border-0">
                      <td className="py-2">
                        <Link className="underline-offset-4 hover:underline" to={`/subjects/${j.subject_id}`}>
                          {subjectName_[j.subject_id] ?? j.subject_id}
                        </Link>
                      </td>
                      <td>{userName[j.assignee_id] ?? j.assignee_id}</td>
                      <td>
                        <StateBadge state={j.state} />
                      </td>
                      <td>{j.base_version ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {owner && (
                <form
                  className="mt-4 flex flex-wrap items-end gap-3 border-t pt-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    add.mutate();
                  }}
                >
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="subject-name">{t("jobs.subject")}</Label>
                    <Input id="subject-name" value={subjectName} onChange={(e) => setSubjectName(e.target.value)} required />
                  </div>
                  <label className="flex flex-col gap-1 text-sm">
                    {t("cycles.assignee")}
                    <NativeSelect value={assignee} onChange={(e) => setAssignee(e.target.value)} required>
                      <option value="">—</option>
                      {(users.data ?? []).map((u) => (
                        <option key={u.user_id} value={u.user_id}>
                          {u.name}
                        </option>
                      ))}
                    </NativeSelect>
                  </label>
                  <Button type="submit">{t("cycles.addSubject")}</Button>
                </form>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="snapshots">
          <Card>
            <CardContent className="flex flex-col gap-3 pt-4">
              <ul className="text-sm">
                {(snapshots.data ?? []).map((s) => (
                  <li key={s.name}>
                    <code>{s.name}</code> · {formatDate(s.created)} · {Object.keys(s.versions).length} subjects
                  </li>
                ))}
              </ul>
              {owner && (
                <form
                  className="flex items-end gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    snap.mutate();
                  }}
                >
                  <div className="flex flex-col gap-1">
                    <Label htmlFor="snap-name">{t("cycles.snapshotName")}</Label>
                    <Input id="snap-name" value={snapName} onChange={(e) => setSnapName(e.target.value)} required />
                  </div>
                  <Button type="submit">{t("cycles.createSnapshot")}</Button>
                </form>
              )}
              <label className="flex flex-col gap-1 text-sm">
                {t("cycles.compare")}
                <NativeSelect value={compareRef} onChange={(e) => setCompareRef(e.target.value)} data-testid="compare-select">
                  <option value="">—</option>
                  {snapOptions.map((s) => (
                    <option key={s} value={s}>
                      {cycleName(s.split("/")[0])} / {s.split("/")[1]}
                    </option>
                  ))}
                </NativeSelect>
              </label>
              {compare.data && (
                <ul className="flex flex-col gap-2 text-sm" data-testid="compare-result">
                  {(compare.data as { name: string; change: string; field_changes?: { path: string; change: string; old: unknown; new: unknown }[] }[]).map((r) => (
                    <li key={r.name} className="rounded border p-2">
                      <strong>{r.name}</strong> <Badge variant="outline">{r.change}</Badge>
                      <ul className="ml-4 list-disc text-xs">
                        {(r.field_changes ?? []).map((fc) => (
                          <li key={fc.path}>
                            <code>{fc.path}</code> {fc.change}: {JSON.stringify(fc.old)} → {JSON.stringify(fc.new)}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        {owner && (
          <TabsContent value="consolidation">
            <Card>
              <CardContent className="grid gap-4 pt-4 md:grid-cols-3" data-testid="consolidation">
                <section>
                  <h3 className="mb-2 font-semibold">{t("cycles.coverage")}</h3>
                  <ul className="text-sm">
                    {Object.entries(consolidation.data?.coverage ?? {}).map(([f, v]) => (
                      <li key={f} className="flex items-center gap-2">
                        <code className="w-32">{f}</code>
                        <span className="h-2 flex-1 rounded bg-slate-200" aria-hidden>
                          <span className="block h-2 rounded bg-slate-900" style={{ width: `${v * 100}%` }} />
                        </span>
                        <span>{Math.round(v * 100)}%</span>
                      </li>
                    ))}
                  </ul>
                </section>
                <section>
                  <h3 className="mb-2 font-semibold">{t("cycles.gaps")}</h3>
                  <ul className="list-disc pl-4 text-sm">
                    {(consolidation.data?.gaps ?? []).map((g, i) => (
                      <li key={i}>{g.message}</li>
                    ))}
                  </ul>
                </section>
                <section>
                  <h3 className="mb-2 font-semibold">{t("cycles.contradictions")}</h3>
                  <ul className="list-disc pl-4 text-sm">
                    {(consolidation.data?.contradictions ?? []).map((g, i) => (
                      <li key={i}>
                        <Badge variant="danger">{g.check}</Badge> {g.message}
                      </li>
                    ))}
                  </ul>
                </section>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
