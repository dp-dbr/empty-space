import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api, unwrap } from "@/api/client";
import { ErrorBox, Loading } from "@/components/ErrorBox";
import { StateBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/utils";

type JobRow = { job: { job_id: string; state: string; due_date?: string | null }; subject_name: string; cycle_name: string };

export function JobsPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["jobs"], queryFn: async () => unwrap(await api.GET("/api/v1/jobs")) as JobRow[] });
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h1>{t("jobs.title")}</h1>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {q.isLoading && <Loading />}
        <ErrorBox error={q.error} />
        {q.data && q.data.length === 0 && <p className="text-sm text-slate-600">{t("jobs.empty")}</p>}
        {q.data && q.data.length > 0 && (
          <table className="w-full text-left text-sm">
            <thead className="border-b text-slate-600">
              <tr>
                <th className="py-2">{t("jobs.subject")}</th>
                <th>{t("jobs.cycle")}</th>
                <th>{t("jobs.state")}</th>
                <th>{t("jobs.due")}</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((r) => (
                <tr key={r.job.job_id} className="border-b last:border-0">
                  <td className="py-2">
                    <Link className="font-medium underline-offset-4 hover:underline" to={`/jobs/${r.job.job_id}`}>
                      {r.subject_name}
                    </Link>
                  </td>
                  <td>{r.cycle_name}</td>
                  <td>
                    <StateBadge state={r.job.state} />
                  </td>
                  <td>{formatDate(r.job.due_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
