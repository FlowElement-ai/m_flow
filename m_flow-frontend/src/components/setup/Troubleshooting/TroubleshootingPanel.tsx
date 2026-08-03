"use client";

/**
 * TroubleshootingPanel Component
 *
 * Main container for the Troubleshooting section of the Setup page.
 * Provides diagnostics, common issues, solution guides, and FAQs.
 *
 * Features:
 * - Tabbed navigation (Issues, Diagnostics, Guides, FAQ)
 * - Search across all content
 * - Health-aware issue suggestions
 * - Step-by-step solution guides
 *
 * @example
 * <TroubleshootingPanel onIssueResolved={(id) => console.log(id)} />
 */

import React, { useState, useMemo } from "react";
import { cn } from "@/lib/utils";
import {
  AlertTriangle,
  Stethoscope,
  BookOpen,
  HelpCircle,
  Search,
  X,
} from "lucide-react";
import { useDetailedHealth } from "@/hooks/use-api";

// Components
import { IssueCard, IssueList } from "./IssueCard";
import { DiagnosticTool } from "./DiagnosticTool";
import { SolutionGuideDisplay, GuideCard } from "./SolutionGuide";
import { FAQSection } from "./FAQSection";

// Content
import {
  COMMON_ISSUES,
  SOLUTION_GUIDES,
  FAQS,
  searchIssues,
  getIssuesByService,
  type SolutionGuide,
} from "@/content/troubleshooting";

import type { ProbeKey } from "@/types/setup";

// ============================================================================
// Types
// ============================================================================

export interface TroubleshootingPanelProps {
  /** Callback when an issue is resolved */
  onIssueResolved?: (issueId: string) => void;
  /** Initial active tab */
  initialTab?: TabId;
  /** Additional CSS classes */
  className?: string;
}

type TabId = "issues" | "diagnostics" | "guides" | "faq";

interface Tab {
  id: TabId;
  label: string;
  icon: React.ReactNode;
  badge?: number;
}

// ============================================================================
// Tab Navigation Component
// ============================================================================

interface TabNavigationProps {
  tabs: Tab[];
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

function TabNavigation({ tabs, activeTab, onTabChange }: TabNavigationProps) {
  return (
    <div className="flex border-b border-zinc-800 overflow-x-auto">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={cn(
            "flex items-center gap-2 px-4 py-3 text-[13px] font-medium",
            "border-b-2 transition-colors whitespace-nowrap",
            activeTab === tab.id
              ? "border-zinc-100 text-zinc-100"
              : "border-transparent text-zinc-500 hover:text-zinc-300"
          )}
        >
          {tab.icon}
          {tab.label}
          {tab.badge !== undefined && tab.badge > 0 && (
            <span
              className={cn(
                "px-1.5 py-0.5 rounded-full text-[10px] font-medium",
                activeTab === tab.id
                  ? "bg-red-500/20 text-red-400"
                  : "bg-zinc-800 text-zinc-500"
              )}
            >
              {tab.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

// ============================================================================
// Issues Tab Content
// ============================================================================

interface IssuesTabProps {
  searchQuery: string;
  suggestedIssues: string[];
}

function IssuesTab({ searchQuery, suggestedIssues }: IssuesTabProps) {
  const filteredIssues = useMemo(() => {
    if (searchQuery) {
      return searchIssues(searchQuery);
    }
    return COMMON_ISSUES;
  }, [searchQuery]);

  // Separate suggested and other issues
  const { suggested, others } = useMemo(() => {
    if (suggestedIssues.length === 0) {
      return { suggested: [], others: filteredIssues };
    }

    const suggested = filteredIssues.filter((i) =>
      suggestedIssues.includes(i.id)
    );
    const others = filteredIssues.filter(
      (i) => !suggestedIssues.includes(i.id)
    );
    return { suggested, others };
  }, [filteredIssues, suggestedIssues]);

  return (
    <div className="space-y-6">
      {/* Suggested Issues (based on health) */}
      {suggested.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={14} className="text-amber-400" />
            <h4 className="text-[14px] font-medium text-zinc-200">
              Suggested Issues
            </h4>
            <span className="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/20 text-amber-400">
              Based on your system status
            </span>
          </div>
          <IssueList issues={suggested} searchQuery={searchQuery} />
        </div>
      )}

      {/* All Issues */}
      <div>
        <h4 className="text-[14px] font-medium text-zinc-200 mb-3">
          {suggested.length > 0 ? "Other Issues" : "Common Issues"}
        </h4>
        <IssueList issues={others} searchQuery={searchQuery} />
      </div>
    </div>
  );
}

// ============================================================================
// Guides Tab Content
// ============================================================================

function GuidesTab() {
  const [selectedGuide, setSelectedGuide] = useState<SolutionGuide | null>(
    null
  );

  if (selectedGuide) {
    return (
      <SolutionGuideDisplay
        guide={selectedGuide}
        onClose={() => setSelectedGuide(null)}
        onComplete={() => {
          // Could show a toast or track completion
          setSelectedGuide(null);
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-[13px] text-zinc-500">
        Step-by-step guides for common tasks and configurations.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {SOLUTION_GUIDES.map((guide) => (
          <GuideCard
            key={guide.id}
            guide={guide}
            onClick={() => setSelectedGuide(guide)}
          />
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function TroubleshootingPanel({
  onIssueResolved,
  initialTab = "issues",
  className,
}: TroubleshootingPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>(initialTab);
  const [searchQuery, setSearchQuery] = useState("");

  // Get health data to suggest relevant issues
  const { data: healthData } = useDetailedHealth({ refetchInterval: false });

  // Calculate suggested issues based on health status
  const suggestedIssueIds = useMemo(() => {
    if (!healthData) return [];

    const failingServices: ProbeKey[] = [];
    Object.entries(healthData.probes).forEach(([key, probe]) => {
      if (probe.verdict === "down" || probe.verdict === "warn") {
        failingServices.push(key as ProbeKey);
      }
    });

    // Get issues related to failing services
    const suggestedIds = new Set<string>();
    failingServices.forEach((service) => {
      getIssuesByService(service).forEach((issue) => {
        suggestedIds.add(issue.id);
      });
    });

    return Array.from(suggestedIds);
  }, [healthData]);

  // Calculate tab badges
  const tabs: Tab[] = [
    {
      id: "issues",
      label: "Common Issues",
      icon: <AlertTriangle size={14} />,
      badge: suggestedIssueIds.length,
    },
    {
      id: "diagnostics",
      label: "Diagnostics",
      icon: <Stethoscope size={14} />,
    },
    {
      id: "guides",
      label: "Guides",
      icon: <BookOpen size={14} />,
    },
    {
      id: "faq",
      label: "FAQ",
      icon: <HelpCircle size={14} />,
    },
  ];

  return (
    <div className={cn("space-y-6 pt-4", className)}>
      {/* Global Search */}
      <div className="relative">
        <Search
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
        />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search issues, guides, and FAQs..."
          className={cn(
            "w-full pl-9 pr-10 py-2.5 rounded-lg text-[13px]",
            "bg-zinc-900 border border-zinc-800",
            "text-zinc-200 placeholder-zinc-500",
            "focus:outline-none focus:ring-1 focus:ring-zinc-600"
          )}
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery("")}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-zinc-500 hover:text-zinc-200"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Tab Navigation */}
      <TabNavigation
        tabs={tabs}
        activeTab={activeTab}
        onTabChange={setActiveTab}
      />

      {/* Tab Content */}
      <div className="min-h-[400px]">
        {activeTab === "issues" && (
          <IssuesTab
            searchQuery={searchQuery}
            suggestedIssues={suggestedIssueIds}
          />
        )}
        {activeTab === "diagnostics" && <DiagnosticTool />}
        {activeTab === "guides" && <GuidesTab />}
        {activeTab === "faq" && (
          <FAQSection faqs={FAQS} searchQuery={searchQuery} showSearch={false} />
        )}
      </div>

      {/* Help Footer */}
      <div className="flex items-center justify-between p-3 rounded-lg bg-zinc-900/50 border border-zinc-800">
        <p className="text-[11px] text-zinc-500">
          Still having issues? Check the{" "}
          <a
            href="https://github.com/FlowElement-xinliuyuansu/m_flow"
            target="_blank"
            rel="noopener noreferrer"
            className="text-zinc-400 hover:text-zinc-200 underline underline-offset-2"
          >
            Troubleshooting Guide
          </a>{" "}
          or{" "}
          <a
            href="https://github.com/FlowElement-xinliuyuansu/m_flow/issues"
            target="_blank"
            rel="noopener noreferrer"
            className="text-zinc-400 hover:text-zinc-200 underline underline-offset-2"
          >
            report an issue
          </a>
          .
        </p>
      </div>
    </div>
  );
}

// ============================================================================
// Display Name
// ============================================================================

TroubleshootingPanel.displayName = "TroubleshootingPanel";

// ============================================================================
// Default Export
// ============================================================================

export default TroubleshootingPanel;
