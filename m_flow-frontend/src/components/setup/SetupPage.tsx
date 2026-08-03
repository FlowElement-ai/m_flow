"use client";

/**
 * SetupPage Component
 *
 * Comprehensive setup interface for M-Flow configuration.
 * Optimized layout with clear visual hierarchy.
 *
 * Layout Structure:
 * - Header: Page title and description
 * - Main: Two-column layout (Config Wizard + Sidebar)
 * - Footer: Resources and help links
 *
 * Design Principles:
 * - Config wizard is the primary focus
 * - Status in compact sidebar
 * - Resources collapsed at bottom
 */

import React, { useState, useCallback, Suspense } from "react";
import { cn } from "@/lib/utils";
import { StatusDashboard } from "./SystemStatus";
import { WizardContainer } from "./ConfigWizard";
import { GettingStartedPanel } from "./GettingStarted";
import { TroubleshootingPanel } from "./Troubleshooting";
import { ErrorBoundary, InlineError } from "@/components/common";
import { SkeletonSection } from "@/components/common/Skeleton";
import type { SetupSection } from "@/types/setup";
import {
  ChevronDown,
  ChevronUp,
  Activity,
  HelpCircle,
  ExternalLink,
} from "lucide-react";

// ============================================================================
// Types
// ============================================================================

export interface SetupPageProps {
  className?: string;
  initialSection?: SetupSection;
}

// ============================================================================
// Compact Status Bar Component
// ============================================================================

function CompactStatusBar() {
  return (
    <ErrorBoundary
      componentName="StatusDashboard"
      fallback={
        <div className="p-3 rounded-lg bg-zinc-900/50 border border-zinc-800 text-[12px] text-zinc-500">
          Unable to load status
        </div>
      }
    >
      <Suspense
        fallback={
          <div className="h-16 rounded-lg bg-zinc-900/50 border border-zinc-800 animate-pulse" />
        }
      >
        <StatusDashboard autoRefresh={true} autoRefreshInterval={30000} />
      </Suspense>
    </ErrorBoundary>
  );
}

// ============================================================================
// Help Links Component
// ============================================================================

function HelpLinks() {
  const links = [
    {
      label: "Documentation",
      href: "https://github.com/FlowElement-xinliuyuansu/m_flow",
      external: true,
    },
    {
      label: "Contact Us",
      href: "mailto:contact@xinliuyuansu.com",
      external: true,
    },
    {
      label: "Report Issue",
      href: "https://github.com/FlowElement-xinliuyuansu/m_flow/issues",
      external: true,
    },
  ];

  return (
    <div className="rounded-lg border border-zinc-800 p-3">
      <div className="flex items-center gap-2 mb-3">
        <HelpCircle size={14} className="text-zinc-400" />
        <span className="text-[13px] font-medium text-zinc-200">Help</span>
      </div>
      <div className="space-y-1">
        {links.map((link) => (
          <a
            key={link.label}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "flex items-center justify-between py-1.5 px-2 -mx-2 rounded",
              "text-[12px] text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50",
              "transition-colors"
            )}
          >
            {link.label}
            {link.external && <ExternalLink size={10} />}
          </a>
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// Collapsible Section Component
// ============================================================================

interface CollapsibleSectionProps {
  title: string;
  description: string;
  isExpanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function CollapsibleSection({
  title,
  description,
  isExpanded,
  onToggle,
  children,
}: CollapsibleSectionProps) {
  return (
    <section className="border border-zinc-800 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className={cn(
          "w-full flex items-center justify-between p-4",
          "text-left hover:bg-zinc-900/50 transition-colors"
        )}
      >
        <div>
          <h3 className="text-[14px] font-medium text-zinc-200">{title}</h3>
          <p className="text-[12px] text-zinc-500 mt-0.5">{description}</p>
        </div>
        {isExpanded ? (
          <ChevronUp size={16} className="text-zinc-500 shrink-0" />
        ) : (
          <ChevronDown size={16} className="text-zinc-500 shrink-0" />
        )}
      </button>
      {isExpanded && (
        <div className="p-4 pt-0 border-t border-zinc-800">{children}</div>
      )}
    </section>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function SetupPage({ className }: SetupPageProps) {
  const [sectionsExpanded, setSectionsExpanded] = useState({
    gettingStarted: false,
    troubleshooting: false,
  });

  const toggleSection = useCallback((section: "gettingStarted" | "troubleshooting") => {
    setSectionsExpanded((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  }, []);

  return (
    <div className={cn("max-w-6xl mx-auto", className)}>
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-lg font-medium text-[var(--text-primary)]">Setup</h1>
        <p className="text-sm text-[var(--text-muted)] mt-1">
          Configure your M-Flow instance, monitor system health, and get started with the API.
        </p>
      </div>

      {/* Main Content: Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        {/* Left Column: Configuration Wizard (Primary) */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/20 overflow-hidden">
            <div className="px-5 py-4 border-b border-zinc-800">
              <h2 className="text-[15px] font-semibold text-zinc-100">
                Configuration
              </h2>
              <p className="text-[12px] text-zinc-500 mt-0.5">
                Set up LLM, embedding, database, and storage
              </p>
            </div>
            <div className="p-5">
              <ErrorBoundary
                componentName="ConfigWizard"
                fallback={
                  <InlineError
                    message="Unable to load configuration wizard"
                    onRetry={() => window.location.reload()}
                  />
                }
              >
                <Suspense fallback={<SkeletonSection />}>
                  <WizardContainer />
                </Suspense>
              </ErrorBoundary>
            </div>
          </div>
        </div>

        {/* Right Column: Status & Quick Actions (Secondary) */}
        <div className="space-y-4">
          {/* System Status */}
          <div className="rounded-lg border border-zinc-800 overflow-hidden">
            <div className="px-4 py-3 border-b border-zinc-800">
              <div className="flex items-center gap-2">
                <Activity size={14} className="text-zinc-400" />
                <span className="text-[13px] font-medium text-zinc-200">
                  System Status
                </span>
              </div>
            </div>
            <div className="p-3">
              <CompactStatusBar />
            </div>
          </div>

          {/* Help Links */}
          <HelpLinks />
        </div>
      </div>

      {/* Resources Section (Collapsed by default) */}
      <div className="space-y-3">
        <CollapsibleSection
          title="Getting Started"
          description="Tutorials, code examples, and documentation"
          isExpanded={sectionsExpanded.gettingStarted}
          onToggle={() => toggleSection("gettingStarted")}
        >
          <ErrorBoundary
            componentName="GettingStarted"
            fallback={
              <div className="text-[12px] text-zinc-500">Unable to load</div>
            }
          >
            <Suspense
              fallback={
                <div className="h-40 rounded bg-zinc-900/50 animate-pulse" />
              }
            >
              <GettingStartedPanel />
            </Suspense>
          </ErrorBoundary>
        </CollapsibleSection>

        <CollapsibleSection
          title="Troubleshooting"
          description="Common issues and diagnostic tools"
          isExpanded={sectionsExpanded.troubleshooting}
          onToggle={() => toggleSection("troubleshooting")}
        >
          <ErrorBoundary
            componentName="Troubleshooting"
            fallback={
              <div className="text-[12px] text-zinc-500">Unable to load</div>
            }
          >
            <Suspense
              fallback={
                <div className="h-40 rounded bg-zinc-900/50 animate-pulse" />
              }
            >
              <TroubleshootingPanel />
            </Suspense>
          </ErrorBoundary>
        </CollapsibleSection>
      </div>

      {/* Footer */}
      <footer className="mt-10 pt-6 border-t border-zinc-800">
        <div className="flex items-center justify-end text-[11px] text-zinc-600">
          <p>M-Flow Setup</p>
        </div>
      </footer>
    </div>
  );
}

SetupPage.displayName = "SetupPage";

export default SetupPage;
