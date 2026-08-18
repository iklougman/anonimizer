import { describe, expect, it, vi, beforeEach } from "vitest";
import {
  AdminApiError,
  createBranch,
  createUser,
  deleteBranch,
  getPermissionMatrix,
  listBranches,
  listUsers,
  putPermissionMatrix,
  updateBranch,
  updateUser,
} from "./admin";

describe("admin API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("listBranches sends the bearer token and returns the parsed list", async () => {
    const branches = [{ id: "b1", name: "Nord", user_count: 2, created_at: "x" }];
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => branches });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listBranches("token-123");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/branches", {
      headers: { Authorization: "Bearer token-123" },
    });
    expect(result).toEqual(branches);
  });

  it("createBranch posts the name and returns the created branch", async () => {
    const created = { id: "b2", name: "Ost", user_count: 0, created_at: "x" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => created });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createBranch("token-123", "Ost");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/branches", {
      method: "POST",
      headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Ost" }),
    });
    expect(result).toEqual(created);
  });

  it("updateBranch sends a PATCH with the new name", async () => {
    const updated = { id: "b2", name: "Renamed", user_count: 0, created_at: "x" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => updated });
    vi.stubGlobal("fetch", fetchMock);

    await updateBranch("token-123", "b2", "Renamed");

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/branches/b2", {
      method: "PATCH",
      headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Renamed" }),
    });
  });

  it("deleteBranch throws an AdminApiError with the backend detail on 409", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: "branch still has users assigned; reassign them first" }),
      })
    );

    await expect(deleteBranch("token-123", "b2")).rejects.toMatchObject({
      status: 409,
      message: "branch still has users assigned; reassign them first",
    });
  });

  it("deleteBranch resolves on 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 204 }));
    await expect(deleteBranch("token-123", "b2")).resolves.toBeUndefined();
  });

  it("listUsers returns the parsed user list", async () => {
    const users = [{ id: "u1", email: "doc@example.com", role: "doctor", branch_id: null, is_active: true, created_at: "x" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => users }));

    const result = await listUsers("token-123");
    expect(result).toEqual(users);
  });

  it("createUser posts the full input and returns the created user with invite status", async () => {
    const created = {
      id: "u2",
      email: "new@example.com",
      role: "doctor",
      branch_id: null,
      is_active: true,
      created_at: "x",
      invite_email_sent: true,
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => created });
    vi.stubGlobal("fetch", fetchMock);

    const result = await createUser("token-123", { email: "new@example.com", role: "doctor" });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/users", {
      method: "POST",
      headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
      body: JSON.stringify({ email: "new@example.com", role: "doctor" }),
    });
    expect(result).toEqual(created);
  });

  it("createUser surfaces a 501 as an AdminApiError (link mode required)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 501,
        json: async () => ({ detail: "Keycloak provisioning is not configured" }),
      })
    );

    await expect(
      createUser("token-123", { email: "x@example.com", role: "staff" })
    ).rejects.toBeInstanceOf(AdminApiError);
  });

  it("updateUser sends a PATCH with only the given fields", async () => {
    const updated = { id: "u2", email: "x@example.com", role: "doctor", branch_id: null, is_active: false, created_at: "x" };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => updated });
    vi.stubGlobal("fetch", fetchMock);

    await updateUser("token-123", "u2", { is_active: false });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/users/u2", {
      method: "PATCH",
      headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: false }),
    });
  });

  it("getPermissionMatrix returns the parsed matrix", async () => {
    const matrix = {
      roles: ["doctor", "staff"],
      permissions: ["conversations:read:branch", "conversations:read:all"],
      defaults: { doctor: [], staff: [] },
      matrix: { doctor: { "conversations:read:branch": false }, staff: {} },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => matrix }));

    const result = await getPermissionMatrix("token-123");
    expect(result).toEqual(matrix);
  });

  it("putPermissionMatrix sends the matrix as a PUT body", async () => {
    const returned = {
      roles: ["doctor", "staff"],
      permissions: ["conversations:read:branch", "conversations:read:all"],
      defaults: { doctor: [], staff: [] },
      matrix: { doctor: { "conversations:read:branch": true }, staff: {} },
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => returned });
    vi.stubGlobal("fetch", fetchMock);

    const result = await putPermissionMatrix("token-123", { doctor: { "conversations:read:branch": true } });

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/admin/permissions", {
      method: "PUT",
      headers: { Authorization: "Bearer token-123", "Content-Type": "application/json" },
      body: JSON.stringify({ matrix: { doctor: { "conversations:read:branch": true } } }),
    });
    expect(result).toEqual(returned);
  });
});
