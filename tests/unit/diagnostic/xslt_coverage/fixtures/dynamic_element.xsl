<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/">

  <xsl:template match="marc:datafield[@tag='340']" mode="instance">
    <xsl:variable name="vClass">
      <xsl:choose>
        <xsl:when test="marc:subfield[@code='a']">bf:BaseMaterial</xsl:when>
        <xsl:otherwise>bf:Carrier</xsl:otherwise>
      </xsl:choose>
    </xsl:variable>
    <xsl:element name="{$vClass}"/>
    <bf:literalThing/>
  </xsl:template>

</xsl:stylesheet>
