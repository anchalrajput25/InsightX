"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowRight,
  CheckCircle2,
  Database,
  FileArchive,
  FileSpreadsheet,
  HardDrive,
  Link2,
  Loader2,
  Plug,
  Server,
  Table2,
  Unplug,
  Upload,
  XCircle,
} from "lucide-react";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioCard, RadioGroup } from "@/components/ui/radio-group";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { useConnection } from "@/components/providers/connection-provider";
import { connectionsApi } from "@/lib/api/connections";
import { errorMessage } from "@/lib/api/client";
import { formatNumber, timeAgo } from "@/lib/format";
import type { ConnectResponse, Connection, DbType, UploadFolderResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const DB_TYPES: { value: DbType; label: string; port: number; hint: string }[] = [
  { value: "postgresql", label: "PostgreSQL", port: 5432, hint: "Most common" },
  { value: "mysql", label: "MySQL", port: 3306, hint: "Also MariaDB" },
  { value: "mssql", label: "SQL Server", port: 1433, hint: "Microsoft" },
];

const KIND_LABEL: Record<string, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  mssql: "SQL Server",
  sqlite: "SQLite",
  uploaded_folder: "PostgreSQL",
};

function toConnection(response: ConnectResponse, name: string): Connection {
  const kind = response.source_type;
  const tables = (response.schema?.tables ?? []).map((table) => ({
    name: table.table_name,
    rows: table.row_count,
    columns: table.column_count,
  }));
  return {
    id: response.connection_id,
    label: `${name} (${KIND_LABEL[kind] ?? kind})`,
    kind,
    connectedAt: Date.now(),
    tables,
    links: response.schema?.relationships?.length ?? 0,
  };
}

const serverSchema = z.object({
  db_type: z.enum(["postgresql", "mysql", "mssql"]),
  host: z.string().trim().min(1, "Enter the host, for example localhost."),
  port: z
    .string()
    .trim()
    .refine((value) => value === "" || (/^\d+$/.test(value) && Number(value) > 0 && Number(value) < 65536), "Enter a port between 1 and 65535."),
  database: z.string().trim().min(1, "Enter the database name."),
  username: z.string().trim().min(1, "Enter the user name."),
  password: z.string(),
});
type ServerValues = z.infer<typeof serverSchema>;

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className="text-xs text-critical">
      {message}
    </p>
  );
}

function ServerFields({
  form,
  showType = true,
  prefix,
}: {
  form: ReturnType<typeof useForm<ServerValues>>;
  showType?: boolean;
  prefix: string;
}) {
  const { errors } = form.formState;
  const dbType = form.watch("db_type");
  const port = DB_TYPES.find((type) => type.value === dbType)?.port ?? 5432;
  const field = (name: keyof ServerValues) => ({
    id: `${prefix}-${name}`,
    "aria-invalid": !!errors[name],
    "aria-describedby": errors[name] ? `${prefix}-${name}-error` : undefined,
  });
  return (
    <div className="space-y-5">
      {showType && (
        <div className="space-y-2">
          <Label>Database type</Label>
          <RadioGroup value={dbType} onValueChange={(value) => form.setValue("db_type", value as DbType)} className="grid gap-2 sm:grid-cols-3">
            {DB_TYPES.map((type) => (
              <RadioCard key={type.value} value={type.value}>
                <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-muted-foreground/40 [[data-state=checked]_&]:border-primary">
                  <span className="size-2 rounded-full bg-primary opacity-0 [[data-state=checked]_&]:opacity-100" />
                </span>
                <span>
                  <span className="block text-sm font-medium">{type.label}</span>
                  <span className="block text-xs text-muted-foreground">{type.hint}</span>
                </span>
              </RadioCard>
            ))}
          </RadioGroup>
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-host`}>Host</Label>
          <Input placeholder="localhost" autoComplete="off" {...field("host")} {...form.register("host")} />
          <FieldError id={`${prefix}-host-error`} message={errors.host?.message} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-port`}>Port</Label>
          <Input inputMode="numeric" placeholder={String(port)} {...field("port")} {...form.register("port")} />
          <FieldError id={`${prefix}-port-error`} message={errors.port?.message} />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${prefix}-database`}>Database name</Label>
        <Input placeholder="sales" autoComplete="off" {...field("database")} {...form.register("database")} />
        <FieldError id={`${prefix}-database-error`} message={errors.database?.message} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-username`}>User name</Label>
          <Input autoComplete="username" {...field("username")} {...form.register("username")} />
          <FieldError id={`${prefix}-username-error`} message={errors.username?.message} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${prefix}-password`}>Password</Label>
          <Input type="password" autoComplete="current-password" {...field("password")} {...form.register("password")} />
        </div>
      </div>
    </div>
  );
}

function useServerForm() {
  return useForm<ServerValues>({
    resolver: zodResolver(serverSchema),
    defaultValues: { db_type: "postgresql", host: "localhost", port: "", database: "", username: "", password: "" },
  });
}

function ErrorBox({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="flex gap-2 rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
      <XCircle className="mt-0.5 size-4 shrink-0" /> <span>{message}</span>
    </p>
  );
}

function ServerTab({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const form = useServerForm();
  const [error, setError] = React.useState("");
  const submit = form.handleSubmit(async (values) => {
    setError("");
    try {
      const response = await connectionsApi.connectDatabase({
        db_type: values.db_type,
        host: values.host.trim(),
        port: values.port ? Number(values.port) : undefined,
        database: values.database.trim(),
        username: values.username.trim(),
        password: values.password,
      });
      onConnected(toConnection(response, values.database.trim()));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  });
  return (
    <form onSubmit={submit} noValidate className="space-y-5">
      <ServerFields form={form} prefix="server" />
      <ErrorBox message={error} />
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">InsightX only reads. It never changes your data.</p>
        <Button type="submit" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? <Loader2 className="animate-spin" /> : <Plug />}
          {form.formState.isSubmitting ? "Connecting and reading tables…" : "Connect"}
        </Button>
      </div>
    </form>
  );
}

function DropZone({
  accept,
  file,
  onFile,
  icon: Icon,
  title,
  hint,
}: {
  accept: string;
  file: File | null;
  onFile: (file: File | null) => void;
  icon: typeof Upload;
  title: string;
  hint: string;
}) {
  const [dragging, setDragging] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);
  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        onFile(event.dataTransfer.files[0] ?? null);
      }}
      className={cn(
        "relative flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center transition-colors",
        dragging ? "border-primary bg-primary/5" : "hover:border-foreground/20",
      )}
    >
      <span className="flex size-10 items-center justify-center rounded-lg border bg-card">
        <Icon className="size-5 text-muted-foreground" />
      </span>
      {file ? (
        <>
          <p className="text-sm font-medium">{file.name}</p>
          <p className="text-xs text-muted-foreground">{formatNumber(file.size / 1024 / 1024)} MB</p>
        </>
      ) : (
        <>
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground">{hint}</p>
        </>
      )}
      <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => inputRef.current?.click()}>
        <Upload /> {file ? "Choose another file" : "Choose file"}
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        aria-label={title}
        onChange={(event) => onFile(event.target.files?.[0] ?? null)}
      />
    </div>
  );
}

function SqliteTab({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const [file, setFile] = React.useState<File | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  return (
    <div className="space-y-5">
      <DropZone accept=".db,.sqlite,.sqlite3" file={file} onFile={setFile} icon={HardDrive} title="Drop a SQLite file here" hint=".db, .sqlite or .sqlite3" />
      <ErrorBox message={error} />
      <div className="flex justify-end">
        <Button
          disabled={!file || busy}
          onClick={async () => {
            if (!file) return;
            setBusy(true);
            setError("");
            try {
              const response = await connectionsApi.connectSqlite(file);
              onConnected(toConnection(response, file.name));
            } catch (caught) {
              setError(errorMessage(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Upload />}
          {busy ? "Uploading and reading tables…" : "Upload and connect"}
        </Button>
      </div>
    </div>
  );
}

function FilesTab({ onConnected }: { onConnected: (connection: Connection) => void }) {
  const [file, setFile] = React.useState<File | null>(null);
  const [upload, setUpload] = React.useState<UploadFolderResponse | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const form = useServerForm();

  const inspect = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      setUpload(await connectionsApi.uploadZip(file));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const importFiles = form.handleSubmit(async (values) => {
    if (!upload) return;
    setError("");
    try {
      const response = await connectionsApi.importUpload(upload.session_id, {
        host: values.host.trim(),
        port: values.port ? Number(values.port) : 5432,
        database: values.database.trim(),
        username: values.username.trim(),
        password: values.password,
      });
      const count = response.imported_tables?.length ?? 0;
      toast.success(`Imported ${count} table${count === 1 ? "" : "s"}`);
      onConnected(toConnection(response, values.database.trim()));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  });

  if (!upload) {
    return (
      <div className="space-y-5">
        <DropZone accept=".zip" file={file} onFile={setFile} icon={FileArchive} title="Drop a ZIP of CSV or Excel files" hint="Each file becomes one table" />
        <ErrorBox message={error} />
        <div className="flex justify-end">
          <Button disabled={!file || busy} onClick={inspect}>
            {busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            {busy ? "Reading every file…" : "Check files"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={importFiles} noValidate className="space-y-6">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm font-medium">
            {upload.importable_files} of {upload.total_files} files can be imported
          </p>
          <Button type="button" variant="ghost" size="xs" onClick={() => setUpload(null)}>
            Start over
          </Button>
        </div>
        <ul className="divide-y rounded-lg border">
          {upload.files.map((item) => (
            <li key={item.file_name} className="flex items-center gap-3 px-3 py-2 text-[13px]">
              {item.importable === false ? <XCircle className="size-4 shrink-0 text-critical" /> : <FileSpreadsheet className="size-4 shrink-0 text-success" />}
              <span className="min-w-0 flex-1 truncate">{item.file_name}</span>
              {item.importable === false ? (
                <span className="truncate text-xs text-critical">{item.error}</span>
              ) : (
                <span className="tabular shrink-0 text-xs text-muted-foreground">
                  {formatNumber(item.row_count)} rows · {item.column_count} columns
                </span>
              )}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-3 text-sm font-medium">PostgreSQL database to import into</p>
        <ServerFields form={form} showType={false} prefix="import" />
      </div>
      <ErrorBox message={error} />
      <div className="flex justify-end">
        <Button type="submit" disabled={form.formState.isSubmitting || upload.importable_files === 0}>
          {form.formState.isSubmitting ? <Loader2 className="animate-spin" /> : <Database />}
          {form.formState.isSubmitting ? "Importing…" : "Import and connect"}
        </Button>
      </div>
    </form>
  );
}

function CurrentConnection({ connection, onDisconnect }: { connection: Connection; onDisconnect: () => void }) {
  const router = useRouter();
  const tables = connection.tables ?? [];
  const [busy, setBusy] = React.useState(false);
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0 border-b bg-surface/50">
        <div className="flex items-start gap-3">
          <span className="flex size-10 items-center justify-center rounded-lg border bg-success/10">
            <CheckCircle2 className="size-5 text-success" />
          </span>
          <div>
            <CardTitle className="text-base">{connection.label}</CardTitle>
            <CardDescription>Connected {timeAgo(connection.connectedAt)}</CardDescription>
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                {busy ? <Loader2 className="animate-spin" /> : <Unplug />} Disconnect
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Disconnect this database?</AlertDialogTitle>
                <AlertDialogDescription>InsightX closes its connection. Your data and your chats are not touched, and you can connect again any time.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  destructive
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await connectionsApi.disconnect(connection.id);
                      toast.success("Database disconnected");
                    } catch (caught) {
                      // Already gone on the server (e.g. it restarted): forget it here too.
                      toast.message("Disconnected here", { description: errorMessage(caught) });
                    } finally {
                      setBusy(false);
                      onDisconnect();
                    }
                  }}
                >
                  Disconnect
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Button size="sm" onClick={() => router.push("/ask")}>
            Ask a question <ArrowRight />
          </Button>
        </div>
      </CardHeader>
      {tables.length > 0 && (
        <CardContent className="pt-5">
          <div className="mb-3 flex flex-wrap gap-2">
            <Badge variant="outline">
              <Table2 /> {tables.length} tables
            </Badge>
            <Badge variant="outline">
              <Link2 /> {connection.links ?? 0} links
            </Badge>
            <Badge variant="outline">
              <Server /> {formatNumber(tables.reduce((sum, table) => sum + (table.rows ?? 0), 0))} rows
            </Badge>
          </div>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {tables.slice(0, 30).map((table) => (
              <li key={table.name} className="flex items-center gap-2.5 rounded-md border px-3 py-2">
                <Table2 className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-mono text-[12.5px]">{table.name}</span>
                <span className="tabular shrink-0 text-[11px] text-muted-foreground">{formatNumber(table.rows)} rows</span>
              </li>
            ))}
          </ul>
          {tables.length > 30 && <p className="mt-2 text-xs text-muted-foreground">and {tables.length - 30} more tables</p>}
        </CardContent>
      )}
    </Card>
  );
}

export function ConnectScreen() {
  const { connection, setConnection } = useConnection();
  const queryClient = useQueryClient();
  const [adding, setAdding] = React.useState(false);

  const onConnected = (next: Connection) => {
    setConnection(next);
    setAdding(false);
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    toast.success("Database connected", { description: `${next.tables?.length ?? 0} tables found in ${next.label}.` });
  };

  const showForm = !connection || adding;
  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-8">
      <PageHeader
        title="Database"
        description="Connect the database you want to ask questions about. Only one is active at a time."
        actions={
          connection && !adding ? (
            <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
              <Plug /> Switch database
            </Button>
          ) : null
        }
      />
      <div className="mt-6 space-y-6">
        {connection && !adding && <CurrentConnection connection={connection} onDisconnect={() => setConnection(null)} />}
        {showForm && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{connection ? "Connect a different database" : "Connect a database"}</CardTitle>
              <CardDescription>Choose where your data lives.</CardDescription>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="server">
                <TabsList className="mb-6">
                  <TabsTrigger value="server">
                    <Server className="size-3.5" /> Database server
                  </TabsTrigger>
                  <TabsTrigger value="sqlite">
                    <HardDrive className="size-3.5" /> SQLite file
                  </TabsTrigger>
                  <TabsTrigger value="files">
                    <FileSpreadsheet className="size-3.5" /> CSV or Excel
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="server">
                  <ServerTab onConnected={onConnected} />
                </TabsContent>
                <TabsContent value="sqlite">
                  <SqliteTab onConnected={onConnected} />
                </TabsContent>
                <TabsContent value="files">
                  <FilesTab onConnected={onConnected} />
                </TabsContent>
              </Tabs>
              {adding && (
                <div className="mt-4 border-t pt-4">
                  <Button variant="ghost" size="sm" onClick={() => setAdding(false)}>
                    Cancel
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
