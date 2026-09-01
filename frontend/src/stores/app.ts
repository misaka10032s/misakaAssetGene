import { defineStore, storeToRefs } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import { NetworkStatus } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";
import { useDraftsStore } from "@/stores/app/drafts";
import { useConversationsStore } from "@/stores/app/conversations";
import { useWorkspaceStore } from "@/stores/app/workspace";
import { useLicenseStore } from "@/stores/app/license";
import { useVersionsStore } from "@/stores/app/versions";
import { useTrainingJobsStore } from "@/stores/app/trainingJobs";
import { useConsultantStore } from "@/stores/app/consultant";
import { useIntegrationStore } from "@/stores/app/integration";
import { useLocalLlmStore } from "@/stores/app/localLlm";
import { useTrainingEntitiesStore } from "@/stores/app/trainingEntities";

const isDevDiagnostics = appEnv.diagnosticsEnabled;

/**
 * `useAppStore` — a thin facade over the eleven domain stores under
 * `stores/app/*` (see each module's own doc comment for its scope and
 * dependencies). Kept so none of the 10+ existing call sites across
 * `pages/`/`components/` need to change: every state ref and action this
 * store used to own directly is still reachable at the same property name
 * on `useAppStore()`.
 *
 * State properties are re-exposed via `storeToRefs()` (not plain property
 * access) so they stay LIVE bindings to the underlying domain store — a
 * plain `domainStore.someState` read here would capture only a one-time
 * snapshot of the value, silently breaking reactivity for every template
 * that reads it through this facade.
 *
 * The only logic that lives HERE rather than in a single domain store is
 * cross-store orchestration that would otherwise force a domain store to
 * import something that imports it back (a cycle): `bootstrap` fans out
 * across core/integration/localLlm/conversations/workspace, and
 * `selectProject` composes `core`'s narrow select with a conversation +
 * workspace reload. Every domain store still only ever depends "downward"
 * (toward `core`/`drafts`), so this facade sitting on top introduces no
 * cycle either — see the G4 gate.
 */
export const useAppStore = defineStore("app", () => {
  const coreStore = useAppCoreStore();
  const draftsStore = useDraftsStore();
  const conversationsStore = useConversationsStore();
  const workspaceStore = useWorkspaceStore();
  const licenseStore = useLicenseStore();
  const versionsStore = useVersionsStore();
  const trainingJobsStore = useTrainingJobsStore();
  const consultantStore = useConsultantStore();
  const integrationStore = useIntegrationStore();
  const localLlmStore = useLocalLlmStore();
  const trainingEntitiesStore = useTrainingEntitiesStore();

  const {
    projects,
    currentProjectId,
    projectTypes,
    networkStatus,
    projectSchema,
    lastMessageKey,
    errorMessageKey,
    currentProject,
    currentProjectName,
    networkTone,
  } = storeToRefs(coreStore);
  const { projectDraft, studioDrafts } = storeToRefs(draftsStore);
  const { projectConversations, projectConversationTotals, projectConversationOffsets } =
    storeToRefs(conversationsStore);
  const { projectPlans, projectJobs, projectAssets, assetDrawerOpen, lastBatchResult } = storeToRefs(workspaceStore);
  const { projectLicenseReports } = storeToRefs(licenseStore);
  const { projectVersionGraphs, projectVersionTrees } = storeToRefs(versionsStore);
  const { projectTrainingJobs } = storeToRefs(trainingJobsStore);
  const { consultantResponse, consultantSessions, synopsisSuggestion } = storeToRefs(consultantStore);
  const { integration, workerSmokeResults, networkState, networkStateTone } = storeToRefs(integrationStore);
  const { localLlmStatus, lastDownloadedModel } = storeToRefs(localLlmStore);
  const { projectTrainingEntities } = storeToRefs(trainingEntitiesStore);

  async function loadProjectDetail(projectId: string): Promise<void> {
    await Promise.all([
      conversationsStore.loadProjectConversation(projectId, true),
      workspaceStore.loadProjectWorkspace(projectId),
    ]);
  }

  async function bootstrap(): Promise<void> {
    try {
      if (isDevDiagnostics) {
        console.info("[misaka.app] bootstrap start");
      }
      const [health, projectTypeData, projectsData, schemaData, integrationData, localLlmData] = await Promise.all([
        apiClient.health(),
        apiClient.projectTypes(),
        apiClient.listProjects(),
        apiClient.projectSchema(),
        integrationStore.fetchIntegrationSnapshot(),
        localLlmStore.fetchLocalLlmStatus(),
      ]);

      coreStore.networkStatus =
        health.status === "Core online" ? NetworkStatus.CORE_ONLINE : NetworkStatus.CORE_OFFLINE;
      coreStore.projectTypes = projectTypeData.project_types;
      coreStore.projects = projectsData.projects;
      coreStore.currentProjectId = projectsData.current_project_id;
      coreStore.projectSchema = JSON.stringify(schemaData.schema, null, 2);
      integrationStore.integration = integrationData;
      localLlmStore.localLlmStatus = localLlmData;
      coreStore.errorMessageKey = null;

      if (coreStore.currentProjectId) {
        await loadProjectDetail(coreStore.currentProjectId);
      }

      if (isDevDiagnostics) {
        console.info("[misaka.app] bootstrap complete", {
          networkStatus: coreStore.networkStatus,
          projectCount: coreStore.projects.length,
        });
      }
    } catch (error) {
      coreStore.networkStatus = NetworkStatus.CORE_OFFLINE;
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
      }
      if (isDevDiagnostics) {
        console.warn("[misaka.app] bootstrap failed", error);
      }
    }
  }

  async function selectProject(projectId: string): Promise<void> {
    await coreStore.selectProject(projectId, loadProjectDetail);
  }

  return {
    assetDrawerOpen,
    consultantResponse,
    consultantSessions,
    currentProject,
    currentProjectId,
    currentProjectName,
    errorMessageKey,
    integration,
    lastDownloadedModel,
    lastMessageKey,
    localLlmStatus,
    networkState,
    networkStateTone,
    networkStatus,
    networkTone,
    projectAssets,
    projectLicenseReports,
    projectConversations,
    projectConversationOffsets,
    projectConversationTotals,
    projectDraft,
    projectJobs,
    projectPlans,
    projectTrainingEntities,
    projectTrainingJobs,
    projectVersionGraphs,
    projectVersionTrees,
    projectSchema,
    projectTypes,
    projects,
    studioDrafts,
    synopsisSuggestion,
    workerSmokeResults,
    lastBatchResult,
    bootstrap,
    closeSynopsisSuggestion: consultantStore.closeSynopsisSuggestion,
    createProject: coreStore.createProject,
    createProjectTrainingJob: trainingJobsStore.createProjectTrainingJob,
    downloadLocalModel: localLlmStore.downloadLocalModel,
    executeProjectJob: workspaceStore.executeProjectJob,
    executeReadyProjectJobs: workspaceStore.executeReadyProjectJobs,
    getStudioDraft: draftsStore.getStudioDraft,
    importProjectAsset: workspaceStore.importProjectAsset,
    refineAsset: workspaceStore.refineAsset,
    installWorker: integrationStore.installWorker,
    loadIntegrationSnapshot: integrationStore.loadIntegrationSnapshot,
    loadLocalLlmStatus: localLlmStore.loadLocalLlmStatus,
    loadProject: coreStore.loadProject,
    loadProjectConversation: conversationsStore.loadProjectConversation,
    loadProjectLicenseReport: licenseStore.loadProjectLicenseReport,
    loadProjectTrainingWorkspace: trainingJobsStore.loadProjectTrainingWorkspace,
    subscribeTrainingJob: trainingJobsStore.subscribeTrainingJob,
    loadProjectVersionGraph: versionsStore.loadProjectVersionGraph,
    loadProjectVersionTree: versionsStore.loadProjectVersionTree,
    loadProjectVersionDiff: versionsStore.loadProjectVersionDiff,
    loadProjectWorkspace: workspaceStore.loadProjectWorkspace,
    loadProjects: coreStore.loadProjects,
    advanceConsultantSession: consultantStore.advanceConsultantSession,
    optimizeSynopsis: consultantStore.optimizeSynopsis,
    requestProjectClarification: consultantStore.requestProjectClarification,
    resumeConsultantSession: consultantStore.resumeConsultantSession,
    selectProject,
    startConsultantSession: consultantStore.startConsultantSession,
    setAssetDrawerOpen: workspaceStore.setAssetDrawerOpen,
    smokeWorker: integrationStore.smokeWorker,
    startLocalLlm: localLlmStore.startLocalLlm,
    startWorker: integrationStore.startWorker,
    stopWorker: integrationStore.stopWorker,
    updateProjectJob: workspaceStore.updateProjectJob,
    updateProjectDraft: draftsStore.updateProjectDraft,
    updateStudioDraft: draftsStore.updateStudioDraft,
    loadProjectTrainingEntities: trainingEntitiesStore.loadProjectTrainingEntities,
    createCharacter: trainingEntitiesStore.createCharacter,
    updateCharacter: trainingEntitiesStore.updateCharacter,
    deleteCharacter: trainingEntitiesStore.deleteCharacter,
    createDatasetPack: trainingEntitiesStore.createDatasetPack,
    updateDatasetPack: trainingEntitiesStore.updateDatasetPack,
    deleteDatasetPack: trainingEntitiesStore.deleteDatasetPack,
    createTrainingRecipe: trainingEntitiesStore.createTrainingRecipe,
    updateTrainingRecipe: trainingEntitiesStore.updateTrainingRecipe,
    deleteTrainingRecipe: trainingEntitiesStore.deleteTrainingRecipe,
    createLoraPreset: trainingEntitiesStore.createLoraPreset,
    updateLoraPreset: trainingEntitiesStore.updateLoraPreset,
    deleteLoraPreset: trainingEntitiesStore.deleteLoraPreset,
    createI2vRecipe: trainingEntitiesStore.createI2vRecipe,
    updateI2vRecipe: trainingEntitiesStore.updateI2vRecipe,
    deleteI2vRecipe: trainingEntitiesStore.deleteI2vRecipe,
  };
});
