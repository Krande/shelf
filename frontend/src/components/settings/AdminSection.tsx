import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Pencil, Plus, X } from "lucide-react";
import {
  createUser,
  fetchAdminUsers,
  updateUser,
  type AdminUser,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import type { Me, Role } from "@/api/me";

export default function AdminSection({ user: me }: { user: Me }) {
  const qc = useQueryClient();

  const users = useQuery<AdminUser[], ApiError>({
    queryKey: ["admin", "users"],
    queryFn: fetchAdminUsers,
    retry: (_attempt, err) => err.status >= 500,
  });

  const setRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) =>
      updateUser(id, { role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      // The caller may have just changed their own role — /api/me decides
      // whether this tab is even reachable.
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-1 text-sm font-medium">Users</h2>
      <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Everyone with an account on this instance. People appear here on their
        own the first time they sign in — add one by email to get them into the
        member picker before that. Admins can rename anyone and change roles;
        the last remaining admin cannot be demoted.
      </p>

      {users.isLoading && (
        <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
          Loading…
        </p>
      )}
      {users.error && (
        <p className="text-sm text-red-600" role="alert">
          {users.error.status === 403
            ? "You no longer have the admin role."
            : `Could not load users: ${users.error.message}`}
        </p>
      )}

      {users.data && (
        <div
          className="overflow-x-auto rounded border"
          style={{ borderColor: "var(--color-border)" }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr
                className="border-b text-left text-xs uppercase tracking-widest"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
              >
                <th className="px-3 py-2 font-normal">User</th>
                <th className="px-3 py-2 font-normal">Joined</th>
                <th className="px-3 py-2 font-normal">Role</th>
              </tr>
            </thead>
            <tbody>
              {users.data.map((u) => {
                const pending =
                  setRole.isPending && setRole.variables?.id === u.id;
                return (
                  <tr
                    key={u.id}
                    className="border-b last:border-b-0"
                    style={{ borderColor: "var(--color-border)" }}
                  >
                    <td className="px-3 py-2">
                      <NameCell user={u} isMe={u.id === me.id} />
                      <span
                        className="block break-all text-xs"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        {u.email}
                      </span>
                    </td>
                    <td
                      className="whitespace-nowrap px-3 py-2 text-xs"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      {new Date(u.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-3 py-2">
                      <span className="flex items-center gap-2">
                        <select
                          value={u.role}
                          disabled={pending}
                          aria-label={`Role for ${u.email}`}
                          onChange={(e) =>
                            setRole.mutate({
                              id: u.id,
                              role: e.target.value as Role,
                            })
                          }
                          className="rounded border px-2 py-1 text-sm disabled:opacity-50"
                          style={{
                            borderColor: "var(--color-border)",
                            backgroundColor: "var(--color-surface)",
                          }}
                        >
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                        {pending && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        )}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {setRole.error && (
        <p className="mt-3 text-sm text-red-600" role="alert">
          {setRole.error instanceof ApiError && setRole.error.status === 409
            ? "That's the last admin — promote someone else first."
            : `Could not change the role: ${setRole.error.message}`}
        </p>
      )}

      <NewUserForm />
    </section>
  );
}

/**
 * A user's display name, with an admin's pencil to correct it.
 *
 * Nothing keeps the name in step with the identity provider — the OIDC
 * callback reads the name claim only when creating the row — so a typo
 * stays a typo until someone fixes it here.
 */
function NameCell({ user, isMe }: { user: AdminUser; isMe: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user.display_name);

  const rename = useMutation({
    mutationFn: () => updateUser(user.id, { display_name: name.trim() }),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      // The member picker and every byline read the same name, and the
      // header shows the caller's own.
      qc.invalidateQueries({ queryKey: ["directory"] });
      if (isMe) qc.invalidateQueries({ queryKey: ["me"] });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || rename.isPending) return;
    // Nothing changed — close rather than spend a round trip saying so.
    if (trimmed === user.display_name) {
      setEditing(false);
      return;
    }
    rename.mutate();
  }

  function cancel() {
    setName(user.display_name);
    rename.reset();
    setEditing(false);
  }

  if (!editing) {
    return (
      <span className="flex items-center gap-1">
        <span>{user.display_name}</span>
        {isMe && (
          <span
            className="text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            you
          </span>
        )}
        <button
          type="button"
          onClick={() => setEditing(true)}
          aria-label={`Rename ${user.email}`}
          title="Rename"
          className="rounded p-0.5 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <Pencil className="h-3 w-3" />
        </button>
      </span>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex items-center gap-1">
      <input
        required
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") cancel();
        }}
        maxLength={200}
        aria-label={`Display name for ${user.email}`}
        className="min-w-0 flex-1 rounded border px-2 py-0.5 text-sm"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-surface)",
        }}
      />
      <button
        type="submit"
        disabled={rename.isPending}
        aria-label={`Save name for ${user.email}`}
        className="rounded border p-1 hover:opacity-80 disabled:opacity-50"
        style={{ borderColor: "var(--color-border)" }}
      >
        {rename.isPending ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <Check className="h-3 w-3" />
        )}
      </button>
      <button
        type="button"
        onClick={cancel}
        aria-label={`Cancel renaming ${user.email}`}
        className="rounded border p-1 hover:opacity-80"
        style={{ borderColor: "var(--color-border)" }}
      >
        <X className="h-3 w-3" />
      </button>
      {rename.error && (
        <span className="text-xs text-red-600" role="alert">
          Could not rename: {rename.error.message}
        </span>
      )}
    </form>
  );
}

/**
 * Pre-provision an account from an email address.
 *
 * Nothing is synced from the identity provider — a user row is created
 * by the OIDC callback the first time someone actually signs in — so
 * until then a new colleague can't be picked as a space member. This
 * creates the row ahead of that; their first login links onto it by
 * email rather than making a second account.
 */
function NewUserForm() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>("user");

  const create = useMutation({
    mutationFn: () =>
      createUser({
        email: email.trim(),
        display_name: displayName.trim() || undefined,
        role,
      }),
    onSuccess: () => {
      setEmail("");
      setDisplayName("");
      setRole("user");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      // The member picker in Settings -> Spaces reads the same people.
      qc.invalidateQueries({ queryKey: ["directory"] });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (email.trim() && !create.isPending) create.mutate();
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-3 flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
        style={{ borderColor: "var(--color-border)" }}
      >
        <Plus className="h-3.5 w-3.5" />
        Add user
      </button>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="mt-3 rounded border p-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div className="flex flex-wrap gap-1">
        <input
          required
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@example.com"
          aria-label="Email address for the new user"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <input
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Display name (optional)"
          aria-label="Display name for the new user"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <select
          value={role}
          aria-label="Role for the new user"
          onChange={(e) => setRole(e.target.value as Role)}
          className="shrink-0 rounded border px-1.5 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <button
          type="submit"
          disabled={create.isPending}
          className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {create.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Add
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80"
          style={{ borderColor: "var(--color-border)" }}
        >
          Cancel
        </button>
      </div>
      <p className="mt-1 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Use the same address their identity provider sends, or they'll get a
        second account on first sign-in instead of this one. Adding someone
        here doesn't grant them access to the provider — they still need to be
        assigned the app there.
      </p>
      {create.error && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {create.error instanceof ApiError && create.error.status === 409
            ? "Someone with that address already has an account."
            : create.error.message}
        </p>
      )}
    </form>
  );
}
