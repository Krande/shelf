import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { fetchAdminUsers, updateUserRole, type AdminUser } from "@/api/admin";
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
      updateUserRole(id, role),
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
        Everyone with an account on this instance. Admins can change roles; the
        last remaining admin cannot be demoted.
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
                      <span className="block">
                        {u.display_name}
                        {u.id === me.id && (
                          <span
                            className="ml-2 text-xs"
                            style={{ color: "var(--color-text-muted)" }}
                          >
                            you
                          </span>
                        )}
                      </span>
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
    </section>
  );
}
